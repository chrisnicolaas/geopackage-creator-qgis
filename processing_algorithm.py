import os
import urllib.request
import json
import zipfile
import tempfile
import shutil

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsWkbTypes,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsVectorFileWriter,
    QgsCoordinateTransformContext,
    QgsProject
)


class WorldBankNaturalEarthAlgorithm(QgsProcessingAlgorithm):
    OUTPUT_FILE  = 'OUTPUT_FILE'
    RESOLUTION   = 'RESOLUTION'
    COUNTRY_ISO3 = 'COUNTRY_ISO3'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return WorldBankNaturalEarthAlgorithm()

    def name(self):
        return 'geopackage_creator_qgis'

    def displayName(self):
        return self.tr('GeoPackage Creator for QGIS')

    def group(self):
        return self.tr('Database')

    def groupId(self):
        return 'database'

    def shortHelpString(self):
        return self.tr(
            'Downloads Natural Earth boundaries and merges them with country and '
            'economic entity classifications (regions and income levels) pulled '
            'directly from the World Bank database.\n\n'
            'Enter ISO-3 country codes for the creation of a GeoPackage of the selected '
            'countries. Groups of countries (i.e., EU-27 or Latin American & Caribbean) '
            'are also possible. Use comma-separated entries (e.g., ESP,PRT,FRA) to '
            'create 1 single GeoPackage containing all selected countries.\n\n'
            'Output GeoPackage includes:\n'
            '- admin0_countries: polygon boundaries with World Bank attributes\n'
            '- country_capitals: point layer of all capital cities'
        )

    def initAlgorithm(self, config=None):
        # Resolution dropdown
        self.addParameter(
            QgsProcessingParameterEnum(
                self.RESOLUTION,
                self.tr('Natural Earth Resolution'),
                options=[
                    self.tr('1:110m (Low Resolution, Fast)'),
                    self.tr('1:50m  (Medium Resolution)'),
                    self.tr('1:10m  (High Resolution, Large)'),
                ],
                defaultValue=0
            )
        )

        # Optional country ISO-3 filter description
        self.addParameter(
            QgsProcessingParameterString(
                self.COUNTRY_ISO3,
                self.tr('Enter ISO-3 country codes (comma-separated, e.g. ESP,PRT,FRA)'),
                defaultValue='',
                optional=True
            )
        )

        # Output GeoPackage
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_FILE,
                self.tr('Output GeoPackage file'),
                fileFilter='GeoPackage (*.gpkg)'
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        resolution_idx = self.parameterAsEnum(parameters, self.RESOLUTION, context)
        iso3_filter    = self.parameterAsString(parameters, self.COUNTRY_ISO3, context).strip().upper()
        output_file    = self.parameterAsFile(parameters, self.OUTPUT_FILE, context)

        resolutions = ['110m', '50m', '10m']
        res = resolutions[resolution_idx]

        # Build ISO-3 filter set
        iso3_set = set()
        if iso3_filter:
            iso3_set = {c.strip() for c in iso3_filter.split(',') if c.strip()}

        # ── 1. Fetch World Bank data ──────────────────────────────────────────
        feedback.setProgressText('Querying World Bank Country API...')
        wb_url = 'http://api.worldbank.org/v2/country?format=json&per_page=300'
        try:
            req = urllib.request.Request(wb_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                wb_data = json.loads(response.read().decode())
        except Exception as e:
            feedback.reportError(f'Failed to fetch World Bank API: {e}')
            return {}

        if len(wb_data) < 2:
            feedback.reportError('Invalid response from World Bank API.')
            return {}

        wb_dict = {}
        for item in wb_data[1]:
            if not item.get('capitalCity'):
                continue
            iso3 = item.get('id')
            wb_dict[iso3] = {
                'iso2':             item.get('iso2Code', ''),
                'wb_name':          item.get('name', ''),
                'wb_region':        item.get('region', {}).get('value', ''),
                'wb_income_level':  item.get('incomeLevel', {}).get('value', ''),
                'wb_lending_type':  item.get('lendingType', {}).get('value', ''),
                'capital_name':     item.get('capitalCity', ''),
                'capital_lon':      float(item['longitude']) if item.get('longitude') else None,
                'capital_lat':      float(item['latitude'])  if item.get('latitude')  else None,
            }

        feedback.setProgressText(f'Loaded {len(wb_dict)} countries from World Bank.')

        # Apply ISO-3 filter
        if iso3_set:
            wb_dict = {k: v for k, v in wb_dict.items() if k in iso3_set}
            feedback.setProgressText(f'ISO-3 filter: {len(wb_dict)} countries remaining.')

        has_filter = bool(iso3_set)

        # ── 2. Download Natural Earth boundaries ──────────────────────────────
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, 'ne_data.zip')
        ne_url   = f'https://naturalearth.s3.amazonaws.com/{res}_cultural/ne_{res}_admin_0_countries.zip'

        feedback.setProgressText(f'Downloading Natural Earth {res} boundaries...')
        try:
            urllib.request.urlretrieve(ne_url, zip_path)
            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(temp_dir)
        except Exception as e:
            feedback.reportError(f'Download/extract failed: {e}')
            shutil.rmtree(temp_dir)
            return {}

        shp_path = os.path.join(temp_dir, f'ne_{res}_admin_0_countries.shp')
        if not os.path.exists(shp_path):
            feedback.reportError(f'Shapefile not found: {shp_path}')
            shutil.rmtree(temp_dir)
            return {}

        ne_layer = QgsVectorLayer(shp_path, 'ne_countries', 'ogr')
        if not ne_layer.isValid():
            feedback.reportError('Failed to load Natural Earth layer.')
            shutil.rmtree(temp_dir)
            return {}

        feedback.setProgressText('Joining datasets and writing to GeoPackage...')

        iso3_col = 'ADM0_A3' if ne_layer.fields().indexFromName('ADM0_A3') != -1 else 'SOV_A3'

        # ── 3. Write country boundaries layer ────────────────────────────────
        out_fields = QgsFields()
        out_fields.append(QgsField('ne_name',          QVariant.String))
        out_fields.append(QgsField('continent',        QVariant.String))
        out_fields.append(QgsField('subregion',        QVariant.String))
        out_fields.append(QgsField('iso3_code',        QVariant.String))
        out_fields.append(QgsField('wb_name',          QVariant.String))
        out_fields.append(QgsField('wb_region',        QVariant.String))
        out_fields.append(QgsField('wb_income_level',  QVariant.String))
        out_fields.append(QgsField('wb_lending_type',  QVariant.String))
        out_fields.append(QgsField('capital_name',     QVariant.String))

        options                      = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName           = 'GPKG'
        options.layerName            = 'admin0_countries'
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

        transform_context = QgsProject.instance().transformContext()

        writer = QgsVectorFileWriter.create(
            output_file,
            out_fields,
            ne_layer.wkbType(),
            ne_layer.sourceCrs(),
            transform_context,
            options
        )

        if writer.hasError() != QgsVectorFileWriter.NoError:
            feedback.reportError(f'GeoPackage writer error: {writer.errorMessage()}')
            del ne_layer
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
            return {}

        ne_names = ne_layer.fields().names()
        for feature in ne_layer.getFeatures():
            iso3_val = feature[iso3_col]

            # Skip features not in the filter sets
            if has_filter and iso3_val not in wb_dict:
                continue

            wb_info     = wb_dict.get(iso3_val, {})
            new_feature = QgsFeature(out_fields)
            new_feature.setGeometry(feature.geometry())
            new_feature['ne_name']         = feature['NAME']     if 'NAME'     in ne_names else ''
            new_feature['continent']       = feature['CONTINENT'] if 'CONTINENT' in ne_names else ''
            new_feature['subregion']       = feature['SUBREGION'] if 'SUBREGION' in ne_names else ''
            new_feature['iso3_code']       = iso3_val
            new_feature['wb_name']         = wb_info.get('wb_name',         '')
            new_feature['wb_region']       = wb_info.get('wb_region',       '')
            new_feature['wb_income_level'] = wb_info.get('wb_income_level', '')
            new_feature['wb_lending_type'] = wb_info.get('wb_lending_type', '')
            new_feature['capital_name']    = wb_info.get('capital_name',    '')
            writer.addFeature(new_feature)

        del writer

        # ── 4. Write capitals points layer ───────────────────────────────────
        feedback.setProgressText('Writing country capitals layer...')
        cap_fields = QgsFields()
        cap_fields.append(QgsField('country_name',    QVariant.String))
        cap_fields.append(QgsField('iso3_code',       QVariant.String))
        cap_fields.append(QgsField('capital_name',    QVariant.String))
        cap_fields.append(QgsField('wb_region',       QVariant.String))
        cap_fields.append(QgsField('wb_income_level', QVariant.String))

        options_cap                          = QgsVectorFileWriter.SaveVectorOptions()
        options_cap.driverName               = 'GPKG'
        options_cap.layerName                = 'country_capitals'
        options_cap.actionOnExistingFile     = QgsVectorFileWriter.CreateOrOverwriteLayer

        writer_cap = QgsVectorFileWriter.create(
            output_file,
            cap_fields,
            QgsWkbTypes.Point,
            ne_layer.sourceCrs(),
            transform_context,
            options_cap
        )

        for iso3_code, wb_info in wb_dict.items():
            lon = wb_info.get('capital_lon')
            lat = wb_info.get('capital_lat')
            if lon is not None and lat is not None:
                feat = QgsFeature(cap_fields)
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
                feat['country_name']    = wb_info.get('wb_name',         '')
                feat['iso3_code']       = iso3_code
                feat['capital_name']    = wb_info.get('capital_name',    '')
                feat['wb_region']       = wb_info.get('wb_region',       '')
                feat['wb_income_level'] = wb_info.get('wb_income_level', '')
                writer_cap.addFeature(feat)

        del writer_cap

        # Release the shapefile layer lock before deleting temp files
        del ne_layer

        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass  # Temp cleanup failure is non-critical; output is already written
        feedback.setProgressText('GeoPackage created successfully!')

        return {self.OUTPUT_FILE: output_file}

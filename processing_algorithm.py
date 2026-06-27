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
    QgsWkbTypes,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsVectorFileWriter,
    QgsProject
)

class WorldBankNaturalEarthAlgorithm(QgsProcessingAlgorithm):
    # Constants for parameter names
    OUTPUT_FILE = 'OUTPUT_FILE'
    RESOLUTION = 'RESOLUTION'
    
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
        return self.tr('Downloads Natural Earth boundaries and merges them with up-to-date World Bank country classifications (regions, income levels, lending types, capitals), outputting a standardized academic GeoPackage database.')
        
    def initAlgorithm(self, config=None):
        # Dropdown selection for map resolution
        self.addParameter(
            QgsProcessingParameterEnum(
                self.RESOLUTION,
                self.tr('Natural Earth Resolution'),
                options=[
                    self.tr('1:110m (Low Resolution, Fast)'),
                    self.tr('1:50m (Medium Resolution)'),
                    self.tr('1:10m (High Resolution, Large)')
                ],
                defaultValue=0
            )
        )
        
        # Output GeoPackage destination parameter
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_FILE,
                self.tr('Output GeoPackage file'),
                fileFilter='GeoPackage (*.gpkg)'
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        resolution_idx = self.parameterAsEnum(parameters, self.RESOLUTION, context)
        output_file = self.parameterAsFile(parameters, self.OUTPUT_FILE, context)
        
        resolutions = ['110m', '50m', '10m']
        res = resolutions[resolution_idx]
        
        feedback.setProgressText("Querying World Bank Country API for academic classifications...")
        wb_url = "http://api.worldbank.org/v2/country?format=json&per_page=300"
        
        try:
            req = urllib.request.Request(wb_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                wb_data = json.loads(response.read().decode())
        except Exception as e:
            feedback.reportError(f"Failed to fetch World Bank API: {str(e)}")
            return {}
            
        if len(wb_data) < 2:
            feedback.reportError("Invalid response format from World Bank API")
            return {}
            
        wb_countries = wb_data[1]
        
        # Parse World Bank data into a dictionary indexed by ISO-3 country code
        wb_dict = {}
        for item in wb_countries:
            # Only process individual countries (skip regions/aggregates)
            if not item.get('capitalCity'):
                continue
            iso3 = item.get('id')
            wb_dict[iso3] = {
                'iso2': item.get('iso2Code', ''),
                'wb_name': item.get('name', ''),
                'wb_region': item.get('region', {}).get('value', ''),
                'wb_income_level': item.get('incomeLevel', {}).get('value', ''),
                'wb_lending_type': item.get('lendingType', {}).get('value', ''),
                'capital_name': item.get('capitalCity', ''),
                'capital_lon': float(item.get('longitude')) if item.get('longitude') else None,
                'capital_lat': float(item.get('latitude')) if item.get('latitude') else None
            }
            
        feedback.setProgressText(f"Successfully loaded {len(wb_dict)} countries from World Bank.")
        
        # Create a temporary folder for downloading and unzipping Natural Earth data
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, "ne_data.zip")
        
        ne_url = f"https://naturalearth.s3.amazonaws.com/{res}_cultural/ne_{res}_admin_0_countries.zip"
        feedback.setProgressText(f"Downloading Natural Earth boundaries from {ne_url}...")
        
        try:
            urllib.request.urlretrieve(ne_url, zip_path)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
        except Exception as e:
            feedback.reportError(f"Failed to download or extract Natural Earth boundary shapefiles: {str(e)}")
            shutil.rmtree(temp_dir)
            return {}
            
        # Find shapefile
        shp_name = f"ne_{res}_admin_0_countries.shp"
        shp_path = os.path.join(temp_dir, shp_name)
        
        if not os.path.exists(shp_path):
            feedback.reportError(f"Shapefile not found in extract: {shp_path}")
            shutil.rmtree(temp_dir)
            return {}
            
        # Load Shapefile into QGIS memory
        ne_layer = QgsVectorLayer(shp_path, "ne_countries", "ogr")
        if not ne_layer.isValid():
            feedback.reportError("Failed to load Natural Earth shapefile layer.")
            shutil.rmtree(temp_dir)
            return {}
            
        feedback.setProgressText("Joining datasets and writing to GeoPackage...")
        
        # Determine join column (usually ADM0_A3 or SOV_A3 for ISO3 country codes)
        iso3_col = 'ADM0_A3' if ne_layer.fields().indexFromName('ADM0_A3') != -1 else 'SOV_A3'
        
        # Set up output fields for the Country Boundaries layer
        out_fields = QgsFields()
        out_fields.append(QgsField('ne_name', QVariant.String))
        out_fields.append(QgsField('continent', QVariant.String))
        out_fields.append(QgsField('subregion', QVariant.String))
        out_fields.append(QgsField('iso3_code', QVariant.String))
        out_fields.append(QgsField('wb_name', QVariant.String))
        out_fields.append(QgsField('wb_region', QVariant.String))
        out_fields.append(QgsField('wb_income_level', QVariant.String))
        out_fields.append(QgsField('wb_lending_type', QVariant.String))
        out_fields.append(QgsField('capital_name', QVariant.String))
        
        # Vector File Writer for the boundaries layer
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = "admin0_countries"
        
        writer = QgsVectorFileWriter(
            output_file,
            "UTF-8",
            out_fields,
            ne_layer.wkbType(),
            ne_layer.sourceCrs(),
            "GPKG",
            options
        )
        
        if writer.hasError() != QgsVectorFileWriter.NoError:
            feedback.reportError(f"GeoPackage Writer error: {writer.errorMessage()}")
            shutil.rmtree(temp_dir)
            return {}
            
        # Write country boundaries features
        for feature in ne_layer.getFeatures():
            iso3_val = feature[iso3_col]
            wb_info = wb_dict.get(iso3_val, {})
            
            new_feature = QgsFeature(out_fields)
            new_feature.setGeometry(feature.geometry())
            
            # Map fields
            new_feature['ne_name'] = feature['NAME'] if 'NAME' in feature.fields().names() else ''
            new_feature['continent'] = feature['CONTINENT'] if 'CONTINENT' in feature.fields().names() else ''
            new_feature['subregion'] = feature['SUBREGION'] if 'SUBREGION' in feature.fields().names() else ''
            new_feature['iso3_code'] = iso3_val
            new_feature['wb_name'] = wb_info.get('wb_name', '')
            new_feature['wb_region'] = wb_info.get('wb_region', '')
            new_feature['wb_income_level'] = wb_info.get('wb_income_level', '')
            new_feature['wb_lending_type'] = wb_info.get('wb_lending_type', '')
            new_feature['capital_name'] = wb_info.get('capital_name', '')
            
            writer.addFeature(new_feature)
            
        del writer # Closes the boundaries layer writer
        
        # Now create the capitals points layer in the same GeoPackage
        feedback.setProgressText("Writing country capitals layer...")
        cap_fields = QgsFields()
        cap_fields.append(QgsField('country_name', QVariant.String))
        cap_fields.append(QgsField('iso3_code', QVariant.String))
        cap_fields.append(QgsField('capital_name', QVariant.String))
        cap_fields.append(QgsField('wb_region', QVariant.String))
        cap_fields.append(QgsField('wb_income_level', QVariant.String))
        
        options_cap = QgsVectorFileWriter.SaveVectorOptions()
        options_cap.driverName = "GPKG"
        options_cap.layerName = "country_capitals"
        options_cap.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        
        writer_cap = QgsVectorFileWriter(
            output_file,
            "UTF-8",
            cap_fields,
            QgsWkbTypes.Point,
            ne_layer.sourceCrs(),
            "GPKG",
            options_cap
        )
        
        # Write capitals point features
        for iso3_code, wb_info in wb_dict.items():
            lon = wb_info.get('capital_lon')
            lat = wb_info.get('capital_lat')
            
            if lon is not None and lat is not None:
                feat = QgsFeature(cap_fields)
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
                feat['country_name'] = wb_info.get('wb_name', '')
                feat['iso3_code'] = iso3_code
                feat['capital_name'] = wb_info.get('capital_name', '')
                feat['wb_region'] = wb_info.get('wb_region', '')
                feat['wb_income_level'] = wb_info.get('wb_income_level', '')
                
                writer_cap.addFeature(feat)
                
        del writer_cap # Closes the capitals layer writer
        
        # Clean up temporary shapefiles
        shutil.rmtree(temp_dir)
        feedback.setProgressText("GeoPackage compilation completed successfully!")
        
        return {self.OUTPUT_FILE: output_file}

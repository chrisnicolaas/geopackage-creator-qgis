from qgis.core import QgsApplication, QgsProcessingProvider
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis import processing
from .processing_algorithm import WorldBankNaturalEarthAlgorithm


class WorldBankNeGpkgPlugin(object):
    def __init__(self, iface):
        self.iface     = iface
        self.provider  = None
        self.action    = None

    def initGui(self):
        """
        Runs when the plugin is loaded in QGIS.
        - Registers the Processing Algorithm provider (Processing Toolbox).
        - Adds a shortcut entry under the top Plugins menu.
        """
        # Register Processing provider
        self.provider = WorldBankNeGpkgProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        # Add entry to the Plugins top menu
        self.action = QAction('GeoPackage Creator for QGIS', self.iface.mainWindow())
        self.action.setToolTip('Create a GeoPackage with World Bank & Natural Earth data')
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu('&GeoPackage Creator for QGIS', self.action)

    def run(self):
        """Opens the algorithm dialog directly from the Plugins menu."""
        processing.execAlgorithmDialog('geopackage_creator_qgis:geopackage_creator_qgis')

    def unload(self):
        """
        Runs when the plugin is disabled or unloaded in QGIS.
        Removes the menu entry and deregisters the Processing provider.
        """
        self.iface.removePluginMenu('&GeoPackage Creator for QGIS', self.action)
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)


class WorldBankNeGpkgProvider(QgsProcessingProvider):
    """
    Groups our custom algorithms together under a single provider
    inside the QGIS Processing Toolbox panel.
    """
    def id(self):
        return 'geopackage_creator_qgis'

    def name(self):
        return 'GeoPackage Creator for QGIS'

    def icon(self):
        return QgsProcessingProvider.icon(self)

    def loadAlgorithms(self):
        """Adds all algorithms associated with this provider."""
        self.addAlgorithm(WorldBankNaturalEarthAlgorithm())

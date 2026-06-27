from qgis.core import QgsApplication, QgsProcessingProvider
from .processing_algorithm import WorldBankNaturalEarthAlgorithm

class WorldBankNeGpkgPlugin(object):
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initGui(self):
        """
        Runs when the plugin is loaded in QGIS.
        Registers our custom Processing Algorithm provider.
        """
        self.provider = WorldBankNeGpkgProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        """
        Runs when the plugin is disabled or unloaded in QGIS.
        Deregisters the custom Processing Algorithm provider.
        """
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)

class WorldBankNeGpkgProvider(QgsProcessingProvider):
    """
    Groups our custom algorithms together under a single folder/provider
    inside the QGIS Processing Toolbox panel.
    """
    def id(self):
        return 'worldbank_ne_provider'

    def name(self):
        return 'World Bank Academic Base Maps'

    def icon(self):
        return QgsProcessingProvider.icon(self)

    def loadAlgorithms(self):
        """
        Adds all algorithms associated with this provider.
        """
        self.addAlgorithm(WorldBankNaturalEarthAlgorithm())

def classFactory(iface):
    """
    Entry point for QGIS to load the plugin.
    Imports the main plugin class and returns it.
    """
    from .plugin import WorldBankNeGpkgPlugin
    return WorldBankNeGpkgPlugin(iface)

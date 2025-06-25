def classFactory(iface):
    from .cadastral_extractor import CadastralExtractor
    return CadastralExtractor(iface)

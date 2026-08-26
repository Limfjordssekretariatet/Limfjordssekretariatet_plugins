# Interface er en undermappe (Python-pakke) i Vaadomraade_Modeller_v2-pluginnet,
# ikke et selvstændigt plugin. Selve plugin-entry-pointet (classFactory) ligger i
# plugin-rodens __init__.py, som importerer .Interface.modelmappe_plugin direkte.
#
# VIGTIGT: Denne mappe må IKKE have sin egen metadata.txt. En metadata.txt her får
# QGIS' plugin-scanner til at behandle 'Interface' som et separat plugin og forsøge
# at importere 'Vaadomraade_Modeller_v2/Interface' (med skråstreg) → ModuleNotFoundError
# på friske installationer.

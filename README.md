# Cadastral Extractor – QGIS Plugin

## Описание (Bulgarian)

QGIS плъгин за работа с полигонови кадастрални слоеве, включително SHP файлове от КАИС.

- Търсене по атрибут `cadnum`
- Добавяне на имоти чрез търсене или избор от картата
- Експорт на избран имот или на целия списък
- CSV: точки на външните контури в EPSG:7801
- KML: контур и точки в отделни файлове, в EPSG:4326
- DXF: контур в EPSG:7801
- Проверка за дублирани записи и отчет за грешки при масов експорт

## Description (English)

A QGIS plugin for polygon cadastral layers, including SHP files from the Bulgarian cadastre/KAIS.

- Search by `cadnum`
- Add parcels by search or map click
- Export one parcel or the entire list
- CSV: exterior-ring points in EPSG:7801
- KML: contour and points in separate files, in EPSG:4326
- DXF: contour in EPSG:7801
- Duplicate detection and error reporting for batch exports

## Compatibility

The code uses `qgis.PyQt` to support the Qt bindings provided by QGIS. The declared QGIS 3.x/4.x range must be validated against actual target installations before publishing.

## License

GNU General Public License v3.0 (GPL-3.0). See `LICENSE`.

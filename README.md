# Cadastral Extractor – QGIS Plugin

## Описание (Bulgarian)

QGIS плъгин за работа с полигонови кадастрални слоеве, включително SHP файлове от българския кадастър/КАИС.

- Търсене по атрибут `cadnum`
- Добавяне на имоти чрез търсене или избор от картата
- Експортиране на отделен имот или на целия списък
- CSV: точки от външния контур в EPSG:7801
- KML: контур и точки в отделни файлове, в EPSG:4326
- DXF: контур в EPSG:7801
- Проверка за дублирани записи и отчет за грешки при масов експорт
- Генериране на скица на имот в PDF формат

## Description (English)

A QGIS plugin for working with polygon cadastral layers, including SHP files from the Bulgarian cadastre/KAIS.

- Search by `cadnum`
- Add parcels by searching or selecting them on the map
- Export an individual parcel or the entire list
- CSV: exterior-ring points in EPSG:7801
- KML: contour and points in separate files, in EPSG:4326
- DXF: contour in EPSG:7801
- Duplicate detection and error reporting for batch exports
- Generate a cadastral property plan in PDF format

## Compatibility

The plugin uses `qgis.PyQt` and is intended for QGIS 3.x. Compatibility with future major QGIS versions should be verified through testing before being declared.

## Version

Current version: **1.2.29**

## License

GNU General Public License v3.0 (GPL-3.0). See `LICENSE`.
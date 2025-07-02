from qgis.PyQt.QtWidgets import QAction, QMessageBox, QFileDialog
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    QgsProject,
    QgsFeatureRequest,
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsWkbTypes,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField
)
from PyQt5 import uic
from PyQt5.QtCore import QVariant
import os

class CadastralExtractor:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None
        self.selected_feature = None
        self.selected_layer = None

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "icons", "icon.png"))
        self.action = QAction(icon, "KAIS Data Extractor", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("KAIS Data Extractor", self.action)

    def unload(self):
        self.iface.removePluginMenu("KAIS Data Extractor", self.action)

    def run(self):
        if not self.dialog:
            ui_path = os.path.join(self.plugin_dir, "forms", "extractor_dialog_base.ui")
            self.dialog = uic.loadUi(ui_path)
            self.dialog.btn_FindParcel.clicked.connect(self.find_parcel)
            self.dialog.btn_Export.clicked.connect(self.export_data)
            self.dialog.progressBar.setVisible(False)

        self.dialog.plugin_info.setText(
            "Посочете слоят с данните за населеното място от КАИС.\n"
            "Въведете идентификатор на имота/сградата:\n "
            "ОПЦИИ ЗА ТЕГЛЕНЕ:\n"
            "-- CSV - Генерирана координатите в координатна система BGS2005/CCS2005 EPSG:7801;\n"
            "-- KML - Генерирани два KML файла - с контура на имота и точките по чупките на имота;\n"
            "-- DXF - Гененерира DXF файл с контура на имота в координатна система BGS2005/CCS2005 EPSG:7801."
        )

        self.dialog.combo_LayerSelect.clear()
        layers = QgsProject.instance().mapLayers().values()
        for layer in layers:
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                self.dialog.combo_LayerSelect.addItem(layer.name(), layer.id())

        self.dialog.show()

    def find_parcel(self):
        self.dialog.progressBar.setVisible(True)
        self.dialog.progressBar.setValue(30)
        cad_id = self.dialog.lineEdit_CadastralID.text().strip()
        if not cad_id:
            QMessageBox.warning(self.dialog, "Грешка", "Моля, въведете идентификатор.")
            return

        layer_index = self.dialog.combo_LayerSelect.currentIndex()
        layer_id = self.dialog.combo_LayerSelect.itemData(layer_index)
        layer = QgsProject.instance().mapLayer(layer_id)

        if not layer or layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            QMessageBox.warning(self.dialog, "Грешка", "Моля, изберете валиден слой с полигони.")
            return

        expression = f'"cadnum" = \'{cad_id}\''
        features = layer.getFeatures(QgsFeatureRequest().setFilterExpression(expression))
        feature = next(features, None)

        if not feature:
            QMessageBox.warning(self.dialog, "Резултат", "Не е намерен имот с този идентификатор.")
            self.dialog.progressBar.setVisible(False)
            return

        self.selected_feature = feature
        self.dialog.progressBar.setValue(30)
        self.selected_layer = QgsVectorLayer("Polygon?crs=EPSG:7801", "Избран имот", "memory")
        self.dialog.progressBar.setValue(60)
        provider = self.selected_layer.dataProvider()
        provider.addAttributes(layer.fields())
        self.selected_layer.updateFields()

        new_feat = QgsFeature(feature)
        provider.addFeatures([new_feat])
        QgsProject.instance().addMapLayer(self.selected_layer)
        # Взимане на атрибутите на новия обект
        from PyQt5.QtCore import QVariant  # добави това горе ако го няма вече
        attributes = new_feat.attributes()
        field_names = [field.name() for field in self.selected_layer.fields()]
        attr_text = "\n".join([
            f"{name}: {value}" for name, value in zip(field_names, attributes) if value not in [None, '', QVariant()]
        ])

        # Показване само ако има какво да се покаже
        if attr_text.strip():
            QMessageBox.information(
                self.dialog,
                "Информация за имота",
                attr_text
            )

        self.iface.messageBar().pushMessage("Успех", "Имотът е намерен и копиран в нов слой.", level=0)
        self.dialog.progressBar.setValue(100)
        self.dialog.progressBar.setVisible(False)

    def export_data(self):
        self.dialog.progressBar.setVisible(True)
        self.dialog.progressBar.setValue(10)

        format = self.dialog.combo_ExportFormat.currentText()
        path = QFileDialog.getSaveFileName(self.dialog, "Запази като", "", f"{format} файл (*.{format.lower()})")[0]
        if not path:
            self.dialog.progressBar.setVisible(False)
            return

        if format == "CSV":
            if not self.selected_layer:
                QMessageBox.warning(self.dialog, "Грешка", "Няма избран имот за експорт.")
                self.dialog.progressBar.setVisible(False)
                return
            
            feat = next(self.selected_layer.getFeatures())
            geom = feat.geometry()
            if geom.isMultipart():
                polygons = geom.asMultiPolygon()
                points = polygons[0][0] if polygons and polygons[0] else []
            else:
                polygon = geom.asPolygon()
                points = polygon[0] if polygon else []

            with open(path, "w", encoding="utf-8") as f:
                f.write("N, X, Y\n")
                for i, pt in enumerate(points):
                    f.write(f"{i+1}, {pt.x():.3f}, {pt.y():.3f}\n")

        elif format == "KML":
            if not self.selected_feature:
                QMessageBox.warning(self.dialog, "Грешка", "Няма избран имот.")
                self.dialog.progressBar.setVisible(False)
                return

            geom = self.selected_feature.geometry()
            if geom.isMultipart():
                polygons = geom.asMultiPolygon()
                points = polygons[0][0] if polygons and polygons[0] else []
            else:
                polygon = geom.asPolygon()
                points = polygon[0] if polygon else []

            if not points:
                QMessageBox.warning(self.dialog, "Грешка", "Не могат да се извлекат точки от геометрията.")
                self.dialog.progressBar.setVisible(False)
                return

            base = path.rsplit(".", 1)[0]
            ext = format.lower()
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = format
            options.fileEncoding = "UTF-8"

            messages = []

            # Контур
            contour_layer = QgsVectorLayer("LineString?crs=EPSG:7801", "Контур", "memory")
            self.dialog.progressBar.setValue(90)
            contour_provider = contour_layer.dataProvider()
            contour_feat = QgsFeature()
            contour_feat.setGeometry(QgsGeometry.fromPolylineXY(points + [points[0]]))
            contour_provider.addFeatures([contour_feat])

            if QgsVectorFileWriter.writeAsVectorFormatV3(contour_layer, base + "_contour." + ext, QgsProject.instance().transformContext(), options)[0] != QgsVectorFileWriter.NoError:
                messages.append("Контур: грешка при запис.")

            # Точки
            point_layer = QgsVectorLayer("Point?crs=EPSG:7801", "Точки", "memory")
            point_provider = point_layer.dataProvider()
            point_provider.addAttributes([QgsField("N", QVariant.Int)])
            point_layer.updateFields()

            point_feats = []
            for i, pt in enumerate(points):
                f = QgsFeature(point_layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(pt))
                f.setAttribute("N", i + 1)
                point_feats.append(f)
            point_provider.addFeatures(point_feats)

            if QgsVectorFileWriter.writeAsVectorFormatV3(point_layer, base + "_points." + ext, QgsProject.instance().transformContext(), options)[0] != QgsVectorFileWriter.NoError:
                messages.append("Точки: грешка при запис.")

            if messages:
                QMessageBox.warning(self.dialog, "Неуспешен експорт", "\n".join(messages))
            else:
                self.iface.messageBar().pushMessage("Успех", f"KML експорт завършен успешно.", level=0)

        elif format == "DXF":
            if not self.selected_feature:
                QMessageBox.warning(self.dialog, "Грешка", "Няма избран имот.")
                self.dialog.progressBar.setVisible(False)
                return

            geom = self.selected_feature.geometry()
            if geom.isMultipart():
                polygons = geom.asMultiPolygon()
                points = polygons[0][0] if polygons and polygons[0] else []
            else:
                polygon = geom.asPolygon()
                points = polygon[0] if polygon else []

            if not points:
                QMessageBox.warning(self.dialog, "Грешка", "Геометрията е невалидна.")
                self.dialog.progressBar.setVisible(False)
                return

            base = path.rsplit(".", 1)[0]
            ext = format.lower()
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = format
            options.fileEncoding = "UTF-8"

            # Контур
            contour_layer = QgsVectorLayer("LineString?crs=EPSG:7801", "Контур", "memory")
            contour_provider = contour_layer.dataProvider()
            contour_feat = QgsFeature()
            contour_feat.setGeometry(QgsGeometry.fromPolylineXY(points + [points[0]]))
            contour_provider.addFeatures([contour_feat])

            if QgsVectorFileWriter.writeAsVectorFormatV3(contour_layer, base + "_contour." + ext, QgsProject.instance().transformContext(), options)[0] != QgsVectorFileWriter.NoError:
                QMessageBox.warning(self.dialog, "Неуспешен експорт", "Грешка при запис на контур.")
            else:
                self.iface.messageBar().pushMessage("Успех", f"DXF експорт завършен успешно.", level=0)

        else:
            QMessageBox.warning(self.dialog, "Грешка", f"Неподдържан формат: {format}")

        self.dialog.progressBar.setValue(100)
        self.dialog.progressBar.setVisible(True)

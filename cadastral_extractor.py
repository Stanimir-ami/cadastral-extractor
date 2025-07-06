from qgis.PyQt.QtWidgets import QAction, QMessageBox, QFileDialog, QWidget, QComboBox, QPushButton, QHBoxLayout, QListWidgetItem, QLabel
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
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
from qgis.gui import QgsMapToolIdentifyFeature
from PyQt5 import uic
from PyQt5.QtCore import QVariant
import os

class CadastralExtractor:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None
        self.selected_features = []  # Списък с избрани имоти
        self.temp_layer = None       # Временен слой с обединени имоти
        self.map_tool = None         # Инструмент за избор чрез карта

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "icons", "icon.png"))
        self.action = QAction(icon, "Cadastral Extractor", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("Cadastral Extractor", self.action)

    def unload(self):
        self.iface.removePluginMenu("Cadastral Extractor", self.action)

    def run(self):
        if not self.dialog:
            ui_path = os.path.join(self.plugin_dir, "forms", "extractor_dialog_base.ui")
            self.dialog = uic.loadUi(ui_path)
            self.dialog.setWindowFlags(self.dialog.windowFlags() | Qt.WindowStaysOnTopHint)
            self.dialog.btn_FindParcel.clicked.connect(self.find_parcel)
            self.dialog.btn_ClearList.clicked.connect(self.clear_parcel_list)
            self.dialog.btn_SelectByClick.clicked.connect(self.enable_map_click_selection)
            self.dialog.btn_RemoveSelected.clicked.connect(self.remove_selected_item)
            self.dialog.progressBar.setVisible(False)

        self.dialog.plugin_info.setText(
            "Инструментът работи като се посочи слой, изтеглен от КАИС портала. Въвежда се валиден идентификатор.\n"
            "Можете да посочите и директно от картата съседни имоти\n"
            "Изберете формат и експортирайте всеки имот отделно.\n"
            "CSV - координати в EPSG:7801;\n" 
            "KML - контур + точки;\n" 
            "DXF - контурв EPSG:7801."
        )

        self.dialog.combo_LayerSelect.clear()
        layers = QgsProject.instance().mapLayers().values()
        for layer in layers:
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                self.dialog.combo_LayerSelect.addItem(layer.name(), layer.id())

        self.dialog.list_SelectedParcels.clear()
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

        expression = f"cadnum = '{cad_id}'"
        features = layer.getFeatures(QgsFeatureRequest().setFilterExpression(expression))
        feature = next(features, None)

        if not feature:
            QMessageBox.warning(self.dialog, "Резултат", "Не е намерен имот с този идентификатор.")
            self.dialog.progressBar.setVisible(False)
            return

        layer.selectByIds([feature.id()])
        canvas = self.iface.mapCanvas()
        canvas.setExtent(feature.geometry().boundingBox())
        canvas.refresh()

        # Показване на атрибути
        attributes = feature.attributes()
        field_names = [field.name() for field in layer.fields()]
        attr_text = "\n".join([
            f"{name}: {value}" for name, value in zip(field_names, attributes)
            if value not in [None, '', QVariant()]
        ])
        if attr_text.strip():
            QMessageBox.information(self.dialog, "Атрибути на обекта", attr_text)

        self.add_feature_to_list(feature, cad_id)

    def add_feature_to_list(self, feature, cad_id):
        self.selected_features.append((feature, cad_id))

        item_widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)

        label = QLabel(f"Имот: {cad_id}")
        layout.addWidget(label)

        combo = QComboBox()
        combo.addItems(["CSV", "KML", "DXF"])
        layout.addWidget(combo)

        export_btn = QPushButton("Експорт")
        export_btn.clicked.connect(lambda _, f=feature, fmt_combo=combo: self.export_individual(f, fmt_combo.currentText()))
        layout.addWidget(export_btn)

        item_widget.setLayout(layout)
        item = QListWidgetItem()
        item.setSizeHint(item_widget.sizeHint())
        self.dialog.list_SelectedParcels.addItem(item)
        self.dialog.list_SelectedParcels.setItemWidget(item, item_widget)

        if not self.temp_layer:
            self.temp_layer = QgsVectorLayer("Polygon?crs=EPSG:7801", "Избрани имоти", "memory")
            self.temp_layer.dataProvider().addAttributes(feature.fields())
            self.temp_layer.updateFields()
            QgsProject.instance().addMapLayer(self.temp_layer)

        self.temp_layer.dataProvider().addFeatures([feature])
        self.temp_layer.updateExtents()

        self.dialog.progressBar.setValue(100)
        self.dialog.progressBar.setVisible(False)
        self.iface.messageBar().pushMessage("Добавено", "Имотът е добавен в списъка.", level=0)

    def clear_parcel_list(self):
        self.selected_features = []
        self.dialog.list_SelectedParcels.clear()
        if self.temp_layer:
            QgsProject.instance().removeMapLayer(self.temp_layer.id())
            self.temp_layer = None
        self.iface.messageBar().pushMessage("Изчистено", "Списъкът е изчистен.", level=0)

    def remove_selected_item(self):
        row = self.dialog.list_SelectedParcels.currentRow()
        if row >= 0:
            self.dialog.list_SelectedParcels.takeItem(row)
            del self.selected_features[row]
            if self.temp_layer:
                QgsProject.instance().removeMapLayer(self.temp_layer.id())
                self.temp_layer = QgsVectorLayer("Polygon?crs=EPSG:7801", "Избрани имоти", "memory")
                self.temp_layer.dataProvider().addAttributes(self.selected_features[0][0].fields())
                self.temp_layer.updateFields()
                self.temp_layer.dataProvider().addFeatures([f for f, _ in self.selected_features])
                self.temp_layer.updateExtents()
                QgsProject.instance().addMapLayer(self.temp_layer)
                self.iface.mapCanvas().refresh()

    def enable_map_click_selection(self):
        layer_index = self.dialog.combo_LayerSelect.currentIndex()
        layer_id = self.dialog.combo_LayerSelect.itemData(layer_index)
        layer = QgsProject.instance().mapLayer(layer_id)

        if not layer or layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            QMessageBox.warning(self.dialog, "Грешка", "Моля, изберете валиден слой с полигони.")
            return

        self.map_tool = QgsMapToolIdentifyFeature(self.iface.mapCanvas())
        self.map_tool.setLayer(layer)
        self.map_tool.featureIdentified.connect(lambda f: self.on_feature_clicked(f, layer))
        self.iface.mapCanvas().setMapTool(self.map_tool)
        self.dialog.show()
        self.iface.messageBar().pushMessage("Активен режим", "Кликнете върху карта, за да изберете имот", level=0)

    def on_feature_clicked(self, feature, layer):
        layer.selectByIds([feature.id()])
        canvas = self.iface.mapCanvas()
        canvas.setExtent(feature.geometry().boundingBox())
        canvas.refresh()

        attributes = feature.attributes()
        field_names = [field.name() for field in layer.fields()]
        attr_text = "\n".join([
            f"{name}: {value}" for name, value in zip(field_names, attributes)
            if value not in [None, '', QVariant()]
        ])
        if attr_text.strip():
            QMessageBox.information(self.dialog, "Атрибути на обекта", attr_text)

        cad_id = feature["cadnum"] if "cadnum" in feature.fields().names() else "(без ID)"
        self.add_feature_to_list(feature, cad_id)
        self.iface.mapCanvas().unsetMapTool(self.map_tool)

    def export_individual(self, feature, format):
        path = QFileDialog.getSaveFileName(self.dialog, "Запази като", "", f"{format} файл (*.{format.lower()})")[0]
        if not path:
            return

        geom = feature.geometry()
        if geom.isMultipart():
            polygons = geom.asMultiPolygon()
            points = polygons[0][0] if polygons and polygons[0] else []
        else:
            polygon = geom.asPolygon()
            points = polygon[0] if polygon else []

        if not points:
            QMessageBox.warning(self.dialog, "Грешка", "Геометрията е невалидна.")
            return

        base = path.rsplit(".", 1)[0]
        ext = format.lower()
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = format
        options.fileEncoding = "UTF-8"

        if format == "CSV":
            with open(path, "w", encoding="utf-8") as f:
                f.write("Номер, X, Y\n")
                for i, pt in enumerate(points):
                    f.write(f"{i+1}, {pt.x():.3f}, {pt.y():.3f}\n")

        elif format in ["KML", "DXF"]:
            contour_layer = QgsVectorLayer("LineString?crs=EPSG:7801", "Контур", "memory")
            contour_provider = contour_layer.dataProvider()
            line_feat = QgsFeature()
            line_feat.setGeometry(QgsGeometry.fromPolylineXY(points + [points[0]]))
            contour_provider.addFeatures([line_feat])
            QgsVectorFileWriter.writeAsVectorFormatV3(contour_layer, base + "_contour." + ext, QgsProject.instance().transformContext(), options)

            if format == "KML":
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
                QgsVectorFileWriter.writeAsVectorFormatV3(point_layer, base + "_points." + ext, QgsProject.instance().transformContext(), options)

        self.iface.messageBar().pushMessage("Експорт", f"Експортът завършен успешно като {format}.", level=0)

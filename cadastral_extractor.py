import os
import csv
import logging

logger = logging.getLogger(__name__)

from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt, QVariant, QVariant, QUrl, QSize, QTimer
from qgis.PyQt.QtWidgets import (
    QAction, QMessageBox, QFileDialog, QWidget, QComboBox, QPushButton,
    QHBoxLayout, QListWidgetItem, QLabel, QVBoxLayout, QProgressBar,
    QAbstractItemView, QToolButton, QGridLayout, QApplication, QDialog, QFrame,
    QScrollArea, QPlainTextEdit
)
from qgis.PyQt.QtGui import QIcon, QDesktopServices, QFont, QColor
from qgis.core import (
    Qgis, QgsProject, QgsFeatureRequest, QgsVectorLayer, QgsVectorFileWriter,
    QgsWkbTypes, QgsFeature, QgsGeometry, QgsPointXY, QgsField,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsVectorLayer,
    QgsRectangle, QgsPrintLayout, QgsLayoutItemMap, QgsLayoutItemLabel,
    QgsLayoutPoint, QgsLayoutSize, QgsLayoutExporter, QgsUnitTypes,
    QgsLayoutItemScaleBar, QgsLayoutItemShape, QgsLayoutItemPage,
    QgsVectorLayer, QgsField, QgsGeometry, QgsPointXY, QgsFeature,
    QgsFillSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling, QgsProperty, QgsTextBufferSettings, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsLayoutMeasurement
)
from qgis.gui import QgsMapTool


class CadastralPickMapTool(QgsMapTool):
    """Safe canvas-click picker that avoids QgsMapToolIdentifyFeature."""

    def __init__(self, canvas, layer, callback, cancel_callback=None):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.callback = callback
        self.cancel_callback = cancel_callback
        self.setCursor(Qt.CursorShape.CrossCursor)

    def keyPressEvent(self, event):
        # Escape is a predictable way to leave map-picking mode.
        if event.key() == Qt.Key.Key_Escape:
            if self.cancel_callback:
                self.cancel_callback()
            event.accept()
            return
        super().keyPressEvent(event)

    def iface_message(self, message):
        try:
            self.canvas.messageBar().pushInfo("Cadastral Extractor", message)
        except Exception:
            logger.exception("Failed to display a message on the QGIS message bar.")

    def canvasReleaseEvent(self, event):
        # Right-click cancels; left-click selects a parcel.
        if event.button() == Qt.MouseButton.RightButton:
            if self.cancel_callback:
                self.cancel_callback()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        try:
            if self.layer is None or not self.layer.isValid():
                return

            # QGIS 3.40 / Qt 5 uses event.pos(). Keep a fallback for Qt 6.
            try:
                pixel_pos = event.pos()
            except AttributeError:
                pixel_pos = event.position().toPoint()

            map_point = self.canvas.getCoordinateTransform().toMapCoordinates(
                pixel_pos.x(), pixel_pos.y()
            )
            point_xy = QgsPointXY(map_point)

            # Search a small area around the click to tolerate pixel precision.
            tolerance = max(
                self.canvas.mapUnitsPerPixel() * 6.0,
                1e-9
            )
            search_rect = QgsRectangle(
                point_xy.x() - tolerance,
                point_xy.y() - tolerance,
                point_xy.x() + tolerance,
                point_xy.y() + tolerance
            )

            click_geometry = QgsGeometry.fromPointXY(point_xy)
            candidates = []
            request = QgsFeatureRequest().setFilterRect(search_rect)

            for feature in self.layer.getFeatures(request):
                geometry = feature.geometry()
                if geometry is None or geometry.isEmpty():
                    continue
                if geometry.intersects(click_geometry):
                    candidates.append((0.0, QgsFeature(feature)))
                else:
                    distance = geometry.distance(click_geometry)
                    if distance <= tolerance:
                        candidates.append((distance, QgsFeature(feature)))

            if not candidates:
                self.iface_message(
                    "Не е намерен имот на това място. Кликнете върху имот или натиснете Escape за отказ."
                )
                return

            # If polygons overlap, choose the feature nearest to the click.
            feature = min(candidates, key=lambda item: item[0])[1]
            self.callback(feature, self.layer)

        except Exception as exc:
            # Report Python-level errors without allowing them to escape
            # through the Qt event loop.
            try:
                self.canvas.messageBar().pushWarning(
                    "Cadastral Extractor",
                    "Грешка при посочване от картата: {}".format(exc)
                )
            except Exception:
                logger.exception("Failed to display the map-picking warning.")


class CadastralExtractor:
    """Extract cadastral parcel geometry and points to CSV, KML or DXF."""

    FORMATS = ("CSV", "KML", "DXF")

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None
        # Entries: dict(feature=QgsFeature, cad_id=str, crs=QgsCoordinateReferenceSystem)
        self.selected_features = []
        self.map_tool = None
        self.info_dialog = None
        self.dialog_was_maximized = False
        self.dialog_default_size = QSize(630, 730)

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "icons", "icon.png"))
        self.action = QAction(icon, "Cadastral Extractor", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("Cadastral Extractor", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.map_tool:
            try:
                self.iface.mapCanvas().unsetMapTool(self.map_tool)
                self.map_tool.deleteLater()
                self.map_tool = None
            except Exception:
                logger.exception("Failed to clean up the map tool during plugin unload.")
                self.map_tool = None
        if self.dialog:
            self.dialog.close()
            self.dialog = None
        if self.action:
            self.iface.removePluginMenu("Cadastral Extractor", self.action)
            self.iface.removeToolBarIcon(self.action)

    def run(self):
        if self.dialog is None:
            ui_path = os.path.join(self.plugin_dir, "forms", "extractor_dialog_base.ui")
            self.dialog = uic.loadUi(ui_path)
            self.dialog.finished.connect(self.reset_session)
            self.dialog.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
            self.dialog.btn_FindParcel.clicked.connect(self.find_parcel)
            self.dialog.btn_ClearList.clicked.connect(self.clear_parcel_list)
            self.dialog.btn_SelectByClick.clicked.connect(self.enable_map_click_selection)
            self.dialog.btn_OpenCadastreData.setIcon(
                QIcon(os.path.join(self.plugin_dir, "icons", "logo_agkk.png"))
            )
            self.dialog.btn_OpenCadastreData.setIconSize(QSize(180, 32))
            self.dialog.btn_OpenCadastreData.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl("https://kais.cadastre.bg/bg/OpenData"))
            )
            self.dialog.btn_RemoveSelected.clicked.connect(self.remove_selected_item)
            self.dialog.btn_RemoveSelected.setToolTip(
                "Първо изберете ред от списъка, след което натиснете този бутон."
            )
            self.dialog.list_SelectedParcels.setToolTip(
                "Изберете ред, за да го премахнете. Всеки ред има собствен формат и бутон за експорт."
            )
            self.dialog.btn_ExportAll.clicked.connect(self.export_all)
            self.dialog.btn_ExportAll.setText("Запиши всички...")
            self.dialog.btn_ExportAll.setToolTip("Изберете директория и запишете всички добавени имоти в нея.")
            # The directory is selected on demand; hide the old persistent path field and browse button.
            self.dialog.folderLabel.hide()
            self.dialog.outputFolder.hide()
            self.dialog.btn_BrowseFolder.hide()
            self.dialog.btn_ExportAll.setEnabled(False)
            self.dialog.progressBar.setVisible(False)
            self.dialog.list_SelectedParcels.setSelectionMode(
    QAbstractItemView.SelectionMode.SingleSelection
            )
            self.dialog.list_SelectedParcels.setVerticalScrollBarPolicy(
    Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            self.dialog.list_SelectedParcels.setHorizontalScrollBarPolicy(
    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.dialog.list_SelectedParcels.setMaximumHeight(220)
            self.dialog.list_SelectedParcels.setSpacing(2)
            self.dialog.lineEdit_CadastralID.returnPressed.connect(self.find_parcel)
            self.dialog.outputFolder.setText(
                os.path.join(os.path.expanduser("~"), "Documents")
            )
        self.refresh_layer_list()
        self.update_list_state()
        # Open centered, slightly smaller than the full screen, while keeping all UI sections accessible.
        self.dialog.showNormal()
        self.dialog.resize(630, 730)
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            frame = self.dialog.frameGeometry()
            frame.moveCenter(available.center())
            self.dialog.move(frame.topLeft())
        self.dialog.raise_()
        self.dialog.activateWindow()

    def reset_session(self, *args):
        """Clear session-only user input when the dialog is closed via its X button."""
        self.selected_features = []
        if self.map_tool:
            try:
                self.iface.mapCanvas().unsetMapTool(self.map_tool)
                self.map_tool.deleteLater()
            except Exception:
                logger.exception("Failed to clean up the map tool during session reset.")
            self.map_tool = None
        if self.info_dialog is not None:
            try:
                self.info_dialog.close()
            except Exception:
                logger.exception("Failed to close the information dialog during session reset.")
            self.info_dialog = None
        if self.dialog is not None:
            try:
                self.dialog.list_SelectedParcels.clear()
                self.dialog.lineEdit_CadastralID.clear()
                self.dialog.progressBar.setValue(0)
                self.dialog.progressBar.setVisible(False)
                self.dialog.btn_ExportAll.setEnabled(False)
                self.dialog.combo_LayerSelect.setCurrentIndex(-1)
                # Keep the standard Documents output path, but clear any custom path.
                self.dialog.outputFolder.setText(
                    os.path.join(os.path.expanduser("~"), "Documents")
                )
            except Exception:
                logger.exception("Failed to reset the plugin dialog fields.")

    def refresh_layer_list(self):
        combo = self.dialog.combo_LayerSelect
        previous_id = combo.currentData()
        combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.isValid() and \
                    layer.geometryType() == QgsWkbTypes.GeometryType.Polygon:
                combo.addItem(layer.name(), layer.id())
        if previous_id:
            index = combo.findData(previous_id)
            if index >= 0:
                combo.setCurrentIndex(index)

    def _current_layer(self):
        layer_id = self.dialog.combo_LayerSelect.currentData()
        return QgsProject.instance().mapLayer(layer_id) if layer_id else None

    def _find_feature(self, layer, cad_id):
        if "cadnum" not in layer.fields().names():
            raise ValueError("Избраният слой няма поле 'cadnum'.")
        # Escape single quotes for a QGIS expression.
        safe_id = cad_id.replace("'", "''")
        expression = '"cadnum" = \'' + safe_id + '\''
        return next(layer.getFeatures(QgsFeatureRequest().setFilterExpression(expression)), None)

    def find_parcel(self):
        cad_id = self.dialog.lineEdit_CadastralID.text().strip()
        if not cad_id:
            QMessageBox.warning(self.dialog, "Липсва идентификатор", "Моля, въведете кадастрален идентификатор.")
            return
        layer = self._current_layer()
        if not layer or not layer.isValid() or layer.geometryType() != QgsWkbTypes.GeometryType.Polygon:
            QMessageBox.warning(self.dialog, "Невалиден слой", "Моля, изберете валиден полигонов слой.")
            return
        try:
            feature = self._find_feature(layer, cad_id)
        except Exception as exc:
            QMessageBox.warning(self.dialog, "Грешка при търсене", str(exc))
            return
        if feature is None:
            QMessageBox.information(self.dialog, "Няма резултат", "Не е намерен имот с този идентификатор.")
            return
        self._zoom_to_feature(layer, feature)
        self.add_feature_to_list(feature, str(cad_id), layer.crs())
        self.show_feature_info(feature, layer)

    def _attribute_value(self, feature, field_name):
        if field_name not in feature.fields().names():
            return "—"
        value = feature[field_name]
        if value is None or str(value).strip() == "":
            return "—"
        return str(value)

    def _neighbor_ids(self, layer, feature):
        """Return cadnum values for polygons that touch the selected parcel.

        QgsGeometry.boundary() is not available in some QGIS 3.x builds,
        including the user's QGIS 3.40 environment. QgsGeometry.touches()
        provides the needed adjacency test directly.
        """
        if "cadnum" not in layer.fields().names():
            return []
        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            return []

        request = QgsFeatureRequest().setFilterRect(geom.boundingBox())
        values, seen = [], set()
        for candidate in layer.getFeatures(request):
            if candidate.id() == feature.id():
                continue
            candidate_geom = candidate.geometry()
            if candidate_geom is None or candidate_geom.isEmpty():
                continue
            try:
                is_neighbor = geom.touches(candidate_geom)
            except Exception:
                is_neighbor = False
            if not is_neighbor:
                continue
            cadnum = self._attribute_value(candidate, "cadnum")
            if cadnum != "—" and cadnum not in seen:
                seen.add(cadnum)
                values.append(cadnum)
        return sorted(values)

    def show_feature_info(self, feature, layer):
        fields = [
            ("cadnum", "ПИ"), ("ekattefn", "Населено място"),
            ("immaddr", "Адрес"), ("proptype", "Вид собственост"),
            ("purptype", "Вид територия"), ("usetype", "Начин на трайно ползване"),
            ("validate", "Заповед за одобрение на КККР"),
        ]
        lines = []
        for field_name, label in fields:
            value = self._attribute_value(feature, field_name)
            lines.append("{} {}".format(label, value) if field_name == "cadnum" else "{}: {}".format(label, value))
        neighbors = self._neighbor_ids(layer, feature)
        lines.append("Съседи: {}".format(", ".join(neighbors) if neighbors else "няма установени"))
        message = "\n".join(lines)

        def display_popup():
            try:
                if self.info_dialog is not None:
                    self.info_dialog.close()
                parent = self.dialog if self.dialog is not None else self.iface.mainWindow()
                popup = QDialog(parent)
                popup.setWindowTitle("Информация за имота")
                popup.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
                popup.setMinimumSize(420, 220)
                popup.resize(560, 360)
                outer = QVBoxLayout(popup)
                scroll = QScrollArea(popup)
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.Shape.NoFrame)
                content = QWidget()
                content_layout = QVBoxLayout(content)
                content_layout.setContentsMargins(8, 8, 8, 8)
                text_label = QLabel(message, content)
                text_label.setWordWrap(True)
                text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                text_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                content_layout.addWidget(text_label)
                scroll.setWidget(content)
                outer.addWidget(scroll)
                buttons = QHBoxLayout()
                buttons.addStretch(1)
                ok_button = QPushButton("OK", popup)
                ok_button.clicked.connect(popup.accept)
                buttons.addWidget(ok_button)
                outer.addLayout(buttons)
                self.info_dialog = popup
                popup.setModal(True)
                popup.show()
                popup.raise_()
                popup.activateWindow()
            except Exception as exc:
                self.iface.messageBar().pushWarning("Cadastral Extractor", "Не може да се покаже информацията за имота: {}".format(exc))
        QTimer.singleShot(150, display_popup)

    def _zoom_to_feature(self, layer, feature):
        layer.selectByIds([feature.id()])
        canvas = self.iface.mapCanvas()
        rect = feature.geometry().boundingBox()
        if not rect.isNull():
            rect.scale(1.25)
            canvas.setExtent(rect)
        canvas.refresh()

    def add_feature_to_list(self, feature, cad_id, crs):
        if any(item["cad_id"] == cad_id and item["feature"].id() == feature.id()
               and item["crs"].authid() == crs.authid() for item in self.selected_features):
            self.iface.messageBar().pushMessage(
                "Cadastral Extractor", "Този имот вече е добавен в списъка.", level=1, duration=3
            )
            return
        initial_format = "CSV"
        self.selected_features.append({"feature": QgsFeature(feature), "cad_id": cad_id, "crs": crs, "format": initial_format, "layer": self._current_layer()})
        item_widget = QWidget(self.dialog.list_SelectedParcels)
        layout = QHBoxLayout(item_widget)
        layout.setContentsMargins(6, 4, 6, 4)
        label = QLabel(cad_id, item_widget)
        label.setToolTip("Кадастрален идентификатор")
        layout.addWidget(label, 1)
        combo = QComboBox(item_widget)
        combo.addItems(self.FORMATS)
        combo.setCurrentText(initial_format)
        combo.setMinimumWidth(78)
        combo.setToolTip("Формат за този имот; използва се и при масов експорт")
        combo.currentTextChanged.connect(
            lambda value, index=len(self.selected_features)-1: self._set_entry_format(index, value)
        )
        layout.addWidget(combo)
        export_btn = QPushButton("Експорт", item_widget)
        export_btn.clicked.connect(
            lambda checked=False, index=len(self.selected_features)-1, fmt=combo:
                self.export_individual(index, fmt.currentText())
        )
        layout.addWidget(export_btn)
        print_btn = QPushButton("Принтиране на план", item_widget)
        print_btn.setToolTip("Генерира PDF кадастрален план за този имот.")
        print_btn.clicked.connect(lambda checked=False, index=len(self.selected_features)-1: self.print_plan(index))
        layout.addWidget(print_btn)
        list_item = QListWidgetItem()
        list_item.setSizeHint(QSize(0, 42))
        self.dialog.list_SelectedParcels.addItem(list_item)
        self.dialog.list_SelectedParcels.setItemWidget(list_item, item_widget)
        self.update_list_state()
        self.iface.messageBar().pushMessage(
            "Добавено", "Имотът е добавен в списъка.", level=0, duration=3
        )

    def _set_entry_format(self, index, fmt):
        if 0 <= index < len(self.selected_features):
            self.selected_features[index]["format"] = fmt

    def update_list_state(self):
        count = len(self.selected_features)
        self.dialog.lbl_Count.setText("Добавени имоти: {}".format(count))
        self.dialog.btn_ExportAll.setEnabled(count > 0)
        self.dialog.btn_ClearList.setEnabled(count > 0)
        self.dialog.btn_RemoveSelected.setEnabled(count > 0)

    def clear_parcel_list(self):
        self.selected_features = []
        self.dialog.list_SelectedParcels.clear()
        self.update_list_state()
        self.iface.messageBar().pushMessage("Изчистено", "Списъкът с имоти е изчистен.", level=0, duration=3)

    def remove_selected_item(self):
        row = self.dialog.list_SelectedParcels.currentRow()
        if row < 0:
            QMessageBox.information(self.dialog, "Избор", "Първо изберете имот от списъка.")
            return
        self.dialog.list_SelectedParcels.takeItem(row)
        del self.selected_features[row]
        self._rebuild_list_widgets()
        self.update_list_state()

    def _rebuild_list_widgets(self):
        # Recreate row widgets so each export button points at the correct list entry.
        entries = list(self.selected_features)
        self.dialog.list_SelectedParcels.clear()
        for index, entry in enumerate(entries):
            item_widget = QWidget(self.dialog.list_SelectedParcels)
            layout = QHBoxLayout(item_widget)
            layout.setContentsMargins(6, 4, 6, 4)
            label = QLabel(entry["cad_id"], item_widget)
            layout.addWidget(label, 1)
            combo = QComboBox(item_widget)
            combo.addItems(self.FORMATS)
            combo.setCurrentText(entry.get("format", "CSV"))
            combo.setToolTip("Формат за този имот; използва се и при масов експорт")
            combo.currentTextChanged.connect(
                lambda value, i=index: self._set_entry_format(i, value)
            )
            layout.addWidget(combo)
            button = QPushButton("Експорт", item_widget)
            button.clicked.connect(
                lambda checked=False, i=index, fmt=combo: self.export_individual(i, fmt.currentText())
            )
            layout.addWidget(button)
            print_button = QPushButton("Принтиране на план", item_widget)
            print_button.setToolTip("Генерира PDF кадастрален план за този имот.")
            print_button.clicked.connect(lambda checked=False, i=index: self.print_plan(i))
            layout.addWidget(print_button)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 42))
            self.dialog.list_SelectedParcels.addItem(item)
            self.dialog.list_SelectedParcels.setItemWidget(item, item_widget)


    def print_plan(self, index):
        """Create a printable A4 cadastral plan as a PDF for one selected parcel."""
        if index < 0 or index >= len(self.selected_features):
            return
        entry = self.selected_features[index]
        feature = entry["feature"]
        layer = entry.get("layer")
        if layer is None or not layer.isValid():
            QMessageBox.warning(self.dialog, "Липсващ слой", "Изходният слой вече не е наличен. Добавете имота отново.")
            return
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            QMessageBox.warning(self.dialog, "Грешна геометрия", "Избраният имот няма валидна геометрия.")
            return

        default_path = os.path.join(
            self.dialog.outputFolder.text().strip() or os.path.expanduser("~/Documents"),
            self._safe_filename(entry["cad_id"]) + "_plan.pdf"
        )
        path, _ = QFileDialog.getSaveFileName(
            self.dialog, "Запази кадастралния план", default_path, "PDF документ (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        try:
            project = QgsProject.instance()
            layout = QgsPrintLayout(project)
            layout.initializeDefaults()
            layout.setName("План на имота - {}".format(entry["cad_id"]))
            page = layout.pageCollection().pages()[0]
            page.setPageSize("A4", QgsLayoutItemPage.Orientation.Portrait)

            # Temporary overlay layers: highlighted parcel outline and numbered contour points.
            crs_id = "EPSG:7801"
            overlay_crs = QgsCoordinateReferenceSystem(crs_id)
            outline_layer = QgsVectorLayer("Polygon?crs={}".format(crs_id), "Избран имот", "memory")
            outline_layer.dataProvider().addAttributes([QgsField("cadnum", QVariant.String)])
            outline_layer.updateFields()
            outline_feature = QgsFeature(outline_layer.fields())
            outline_geom = QgsGeometry(geometry)
            if layer.crs().authid() != crs_id:
                transform = QgsCoordinateTransform(layer.crs(), overlay_crs, project)
                outline_geom.transform(transform)
            outline_feature.setGeometry(outline_geom)
            outline_feature["cadnum"] = entry["cad_id"]
            outline_layer.dataProvider().addFeatures([outline_feature])
            outline_layer.updateExtents()
            outline_symbol = QgsFillSymbol.createSimple({
                "color": "255,255,255,0", "outline_color": "210,35,35,255",
                "outline_width": "0.8", "outline_width_unit": "MM", "style": "no"
            })
            outline_layer.renderer().setSymbol(outline_symbol)

            contour_points = [point for ring in self._geometry_points(geometry) for point in ring]
            vertex_count = len(contour_points)
            show_numbered_points = vertex_count <= 40

            points_layer = QgsVectorLayer("Point?crs={}".format(crs_id), "Номера на точките", "memory")
            points_layer.dataProvider().addAttributes([QgsField("номер", QVariant.Int)])
            points_layer.updateFields()
            point_features = []
            point_no = 0
            transform_points = None
            if layer.crs().authid() != crs_id:
                transform_points = QgsCoordinateTransform(layer.crs(), overlay_crs, project)
            if show_numbered_points:
                for point in contour_points:
                    point_no += 1
                    pt = QgsPointXY(point.x(), point.y())
                    if transform_points:
                        pt = transform_points.transform(pt)
                    pf = QgsFeature(points_layer.fields())
                    pf.setGeometry(QgsGeometry.fromPointXY(pt))
                    pf["номер"] = point_no
                    point_features.append(pf)
            points_layer.dataProvider().addFeatures(point_features)
            points_layer.updateExtents()
            points_layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
                "name": "cross", "color": "35,35,35,255", "outline_color": "255,255,255,255",
                "outline_width": "0.25", "size": "1.4", "size_unit": "MM"
            }))
            label_settings = QgsPalLayerSettings()
            label_settings.fieldName = "номер"
            label_settings.placement = Qgis.LabelPlacement.OrderedPositionsAroundPoint
            label_settings.dist = 1.2
            text_format = QgsTextFormat()
            text_format.setFont(QFont("Arial", 8))
            text_format.setSize(8)
            text_format.setColor(QColor(20, 20, 20))
            text_buffer = QgsTextBufferSettings()
            text_buffer.setEnabled(True)
            text_buffer.setSize(1.1)
            text_buffer.setColor(QColor(255, 255, 255))
            text_format.setBuffer(text_buffer)
            label_settings.setFormat(text_format)
            points_layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
            points_layer.setLabelsEnabled(True)

            # Header
            title = QgsLayoutItemLabel(layout)
            title.setText("Данни за имот")
            title.setFont(QFont("Arial", 15, QFont.Weight.Bold))
            title.setHAlign(Qt.AlignmentFlag.AlignHCenter)
            title.attemptMove(QgsLayoutPoint(10, 7))
            title.attemptResize(QgsLayoutSize(190, 10))
            layout.addLayoutItem(title)

            cad_label = QgsLayoutItemLabel(layout)
            cad_label.setText("Поземлен имот: {}".format(entry["cad_id"]))
            cad_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            cad_label.attemptMove(QgsLayoutPoint(10, 18))
            cad_label.attemptResize(QgsLayoutSize(190, 7))
            layout.addLayoutItem(cad_label)

            # Main map frame
            map_item = QgsLayoutItemMap(layout)
            map_item.attemptMove(QgsLayoutPoint(10, 28))
            map_item.attemptResize(QgsLayoutSize(190, 105))
            map_item.setLayers(([points_layer] if show_numbered_points else []) + [outline_layer, layer])
            map_item.setKeepLayerSet(True)
            extent = QgsRectangle(outline_geom.boundingBox())
            extent.scale(1.35)
            map_item.zoomToExtent(extent)
            # Choose a rounded, readable scale that fits the parcel in the map frame.
            required_scale = map_item.scale()
            standard_scales = [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 25000, 50000, 100000, 250000]
            if required_scale <= standard_scales[0]:
                fitted_scale = standard_scales[0]
            else:
                fitted_scale = next((scale for scale in standard_scales if scale >= required_scale), None)
                if fitted_scale is None:
                    import math
                    magnitude = 10 ** int(math.floor(math.log10(required_scale)))
                    fitted_scale = next(multiplier * magnitude for multiplier in (1, 2, 2.5, 5, 10)
                                        if multiplier * magnitude >= required_scale)
            map_item.setScale(fitted_scale)
            layout.addLayoutItem(map_item)
            map_item.setFrameEnabled(True)

            scale_text = QgsLayoutItemLabel(layout)
            scale_text.setText("Мащаб 1:{}".format(fitted_scale))
            scale_text.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            scale_text.setHAlign(Qt.AlignmentFlag.AlignHCenter)
            scale_text.attemptMove(QgsLayoutPoint(10, 134))
            scale_text.attemptResize(QgsLayoutSize(190, 6))
            layout.addLayoutItem(scale_text)

            # North indicator
            north = QgsLayoutItemLabel(layout)
            north.setText("N ↑")
            north.setFont(QFont("Arial", 12, QFont.Weight.Bold))
            north.setHAlign(Qt.AlignmentFlag.AlignHCenter)
            north.attemptMove(QgsLayoutPoint(185, 31))
            north.attemptResize(QgsLayoutSize(12, 10))
            layout.addLayoutItem(north)

            # Attribute information from the popup
            fields = [
                ("cadnum", "ПИ"), ("ekattefn", "Населено място"),
                ("immaddr", "Адрес"), ("proptype", "Вид собственост"),
                ("purptype", "Вид територия"), ("usetype", "Начин на трайно ползване"),
                ("validate", "Заповед за одобрение на КККР"),
            ]
            info_lines = ["ИНФОРМАЦИЯ ЗА ИМОТА"]
            for field_name, label in fields:
                info_lines.append("{}: {}".format(label, self._attribute_value(feature, field_name)))
            neighbors = self._neighbor_ids(layer, feature)
            if len(neighbors) > 40:
                info_lines.append("Броят на съседните имоти надвишава 40. За цялата информация относно съседните имоти проверете в АГКК")
            else:
                info_lines.append("Съседи: {}".format(", ".join(neighbors) if neighbors else "няма установени"))
            info = QgsLayoutItemLabel(layout)
            info.setText("\n".join(info_lines))
            info.setFont(QFont("Arial", 8))
            info.setMode(QgsLayoutItemLabel.Mode.ModeFont)
            info.setMargin(1)
            info.attemptMove(QgsLayoutPoint(10, 142))
            info.attemptResize(QgsLayoutSize(190, 42))
            layout.addLayoutItem(info)

            # Coordinate register: split into two columns when there are more than 20 points.
            if show_numbered_points:
                point_rows = []
                coord_transform = None
                if layer.crs().authid() != "EPSG:7801":
                    coord_transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:7801"), project)
                for point_no, point in enumerate(contour_points, start=1):
                    coord_point = QgsPointXY(point.x(), point.y())
                    if coord_transform:
                        coord_point = coord_transform.transform(coord_point)
                    point_rows.append((point_no, coord_point.x(), coord_point.y()))

                title_line = "Координати на точките по контура на имота (EPSG:7801)"
                if len(point_rows) > 20:
                    # Two compact columns keep all 40 records inside the table at the same font size.
                    half = (len(point_rows) + 1) // 2
                    left_rows = point_rows[:half]
                    right_rows = point_rows[half:]
                    coord_lines = [title_line, "{:>2} {:>12} {:>12}    {:>2} {:>12} {:>12}".format("№", "X", "Y", "№", "X", "Y")]
                    for i in range(half):
                        left = left_rows[i]
                        left_text = "{:>2} {:>12.3f} {:>12.3f}".format(*left)
                        if i < len(right_rows):
                            right_text = "{:>2} {:>12.3f} {:>12.3f}".format(*right_rows[i])
                            coord_lines.append(left_text + "    " + right_text)
                        else:
                            coord_lines.append(left_text)
                else:
                    coord_lines = [title_line, "№          X                         Y"]
                    for row in point_rows:
                        coord_lines.append("{:<4} {:>18.3f} {:>18.3f}".format(*row))
                coord_text = "\n".join(coord_lines)
            else:
                coord_text = ("Точките са над 40. За таблица с координатите на всички точки "
                              "изтеглете имота като CSV.")
            coord_label = QgsLayoutItemLabel(layout)
            coord_label.setText(coord_text)
            coord_label.setFont(QFont("Courier New", 8))
            coord_label.setMargin(2)
            coord_label.setFrameEnabled(True)
            coord_label.setFrameStrokeColor(QColor(100, 100, 100))
            coord_label.setFrameStrokeWidth(QgsLayoutMeasurement(0.25))
            coord_label.attemptMove(QgsLayoutPoint(10, 186))
            coord_label.attemptResize(QgsLayoutSize(190, 90))
            layout.addLayoutItem(coord_label)

            # Scale bar linked to map
            scale_bar = QgsLayoutItemScaleBar(layout)
            scale_bar.setStyle("Single Box")
            scale_bar.setLinkedMap(map_item)
            scale_bar.setUnits(QgsUnitTypes.DistanceUnit.DistanceMeters)
            scale_bar.setUnitsPerSegment(10)
            scale_bar.setNumberOfSegments(2)
            scale_bar.setNumberOfSegmentsLeft(0)
            scale_bar.setUnitLabel("m")
            scale_bar.setFont(QFont("Arial", 7))
            scale_bar.attemptMove(QgsLayoutPoint(12, 128))
            layout.addLayoutItem(scale_bar)

            footer = QgsLayoutItemLabel(layout)
            footer.setText("Разработка: Станимир Димитров - Минно-геоложки университет, катедра Маркшайдерство и геодезия, email: stanimirami777@gmail.com")
            footer.setFont(QFont("Arial", 6))
            footer.setFontColor(QColor(90, 90, 90))
            footer.setHAlign(Qt.AlignmentFlag.AlignHCenter)
            footer.setVAlign(Qt.AlignmentFlag.AlignVCenter)
            footer.attemptMove(QgsLayoutPoint(5, 285))
            footer.attemptResize(QgsLayoutSize(200, 8))
            layout.addLayoutItem(footer)

            exporter = QgsLayoutExporter(layout)
            settings = QgsLayoutExporter.PdfExportSettings()
            settings.dpi = 200
            result = exporter.exportToPdf(path, settings)
            if result != QgsLayoutExporter.ExportResult.Success:
                raise RuntimeError("QGIS не успя да запише PDF файла (код {}).".format(result))
            QMessageBox.information(self.dialog, "Готово", "Кадастралният план е записан:\n{}".format(path))
        except Exception as exc:
            QMessageBox.critical(self.dialog, "Грешка при генериране на план", str(exc))

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self.dialog, "Изберете папка за експорта", self.dialog.outputFolder.text()
        )
        if folder:
            self.dialog.outputFolder.setText(folder)

    def enable_map_click_selection(self):
        layer = self._current_layer()
        if not layer or not layer.isValid() or layer.geometryType() != QgsWkbTypes.GeometryType.Polygon:
            QMessageBox.warning(self.dialog, "Невалиден слой", "Моля, изберете валиден полигонов слой.")
            return
        if "cadnum" not in layer.fields().names():
            QMessageBox.warning(
                self.dialog, "Липсва поле cadnum",
                "Избраният слой няма поле 'cadnum'. Изберете кадастрален полигонов слой, който съдържа това поле."
            )
            return
        canvas = self.iface.mapCanvas()
        self.map_tool = CadastralPickMapTool(
            canvas, layer, self.on_feature_clicked, self.cancel_map_click_selection
        )
        canvas.setMapTool(self.map_tool)
        # Minimize the dialog while the user picks a feature on the map.
        self.dialog_was_maximized = self.dialog.isMaximized()
        self.dialog.showMinimized()
        self.iface.messageBar().pushMessage(
            "Избор от карта", "Кликнете върху имот от избрания слой.", level=0, duration=5
        )

    def cancel_map_click_selection(self):
        """Stop map picking and restore the plugin dialog without adding a parcel."""
        if self.map_tool:
            tool = self.map_tool
            self.map_tool = None
            try:
                self.iface.mapCanvas().unsetMapTool(tool)
                tool.deleteLater()
            except Exception:
                logger.exception(
                    "Failed to clean up the map tool after cancelling map selection."
                )
        if self.dialog:
            self.dialog.showNormal()
            self.dialog.resize(self.dialog_default_size)
            self.dialog.raise_()
            self.dialog.activateWindow()
        self.iface.messageBar().pushMessage(
            "Избор от карта", "Изборът е отменен.", level=0, duration=3
        )

    def on_feature_clicked(self, feature, layer):
        self._zoom_to_feature(layer, feature)
        if "cadnum" not in feature.fields().names() or feature["cadnum"] is None:
            self.cancel_map_click_selection()
            QMessageBox.warning(
                self.dialog, "Липсва идентификатор",
                "Избраният обект няма стойност в полето 'cadnum' и не може да бъде добавен."
            )
            return
        cad_id = str(feature["cadnum"])
        self.add_feature_to_list(feature, cad_id, layer.crs())

        # Stop map picking first, then restore the dialog before showing the popup.
        if self.map_tool:
            tool = self.map_tool
            self.map_tool = None
            self.iface.mapCanvas().unsetMapTool(tool)
            tool.deleteLater()
        if self.dialog:
            self.dialog.showNormal()
            self.dialog.resize(self.dialog_default_size)
            self.dialog.raise_()
            self.dialog.activateWindow()

        self.show_feature_info(feature, layer)

    @staticmethod
    def _geometry_points(geometry):
        """Return exterior-ring points for every polygon part."""
        result = []
        if geometry.isMultipart():
            polygons = geometry.asMultiPolygon()
        else:
            polygon = geometry.asPolygon()
            polygons = [polygon] if polygon else []
        for polygon in polygons:
            if polygon and polygon[0]:
                ring = polygon[0]
                # A polygon ring usually repeats its first vertex at the end.
                if len(ring) > 1 and ring[0] == ring[-1]:
                    ring = ring[:-1]
                result.append(list(ring))
        return result

    @staticmethod
    def _transform_geometry(geometry, source_crs, target_crs):
        result = QgsGeometry(geometry)
        if source_crs.isValid() and target_crs.isValid() and source_crs != target_crs:
            transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
            result.transform(transform)
        return result

    def export_individual(self, index, fmt):
        if index < 0 or index >= len(self.selected_features):
            return
        entry = self.selected_features[index]
        default_name = self._safe_filename(entry["cad_id"])
        filter_text = "{} файл (*.{})".format(fmt, fmt.lower())
        path, _ = QFileDialog.getSaveFileName(
            self.dialog, "Запази като", default_name + "." + fmt.lower(), filter_text
        )
        if not path:
            return
        try:
            self._export_entry(entry, fmt, path)
        except Exception as exc:
            QMessageBox.critical(self.dialog, "Грешка при експорт", str(exc))
            return
        self.iface.messageBar().pushMessage(
            "Експорт", "Експортът на {} е завършен.".format(entry["cad_id"]), level=0, duration=4
        )

    def export_all(self):
        if not self.selected_features:
            return
        folder = QFileDialog.getExistingDirectory(
            self.dialog,
            "Изберете директория за запис на всички имоти",
            os.path.join(os.path.expanduser("~"), "Documents")
        )
        if not folder:
            return
        self.dialog.progressBar.setVisible(True)
        self.dialog.progressBar.setValue(0)
        errors = []
        total = len(self.selected_features)
        for index, entry in enumerate(self.selected_features):
            fmt = entry.get("format", "CSV").upper()
            path = os.path.join(folder, self._safe_filename(entry["cad_id"]) + "." + fmt.lower())
            try:
                self._export_entry(entry, fmt, path)
            except Exception as exc:
                errors.append("{}: {}".format(entry["cad_id"], exc))
            self.dialog.progressBar.setValue(int((index + 1) * 100 / total))
            QApplication.processEvents()
        self.dialog.progressBar.setVisible(False)
        success = total - len(errors)
        message = "Успешно експортирани имоти: {} от {}.\nПапка: {}".format(success, total, folder)
        if errors:
            message += "\n\nПроблеми:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                message += "\n... и още {} грешки.".format(len(errors) - 10)
            QMessageBox.warning(self.dialog, "Експортът приключи с проблеми", message)
        else:
            QMessageBox.information(self.dialog, "Експортът е завършен", message)

    @staticmethod
    def _safe_filename(value):
        name = str(value or "parcel")
        for char in '<>:"/\\|?*':
            name = name.replace(char, "_")
        return name.strip(" .") or "parcel"

    def _export_entry(self, entry, fmt, path):
        feature = entry["feature"]
        source_crs = entry["crs"]
        geometry = feature.geometry()
        if geometry is None or geometry.isNull() or geometry.isEmpty():
            raise ValueError("Обектът няма валидна геометрия.")

        base, _ = os.path.splitext(path)
        fmt = fmt.upper()
        if fmt == "CSV":
            self._export_csv(geometry, source_crs, path)
        elif fmt == "KML":
            self._export_vector(geometry, source_crs, base + "_contour.kml", "KML", "contour")
            self._export_vector(geometry, source_crs, base + "_points.kml", "KML", "points")
        elif fmt == "DXF":
            self._export_vector(geometry, source_crs, path, "DXF", "contour")
        else:
            raise ValueError("Неподдържан формат: {}".format(fmt))

    def _export_csv(self, geometry, source_crs, path):
        # Preserve source coordinates, matching the original plugin's EPSG:7801 output intent.
        target_crs = QgsCoordinateReferenceSystem("EPSG:7801")
        export_geom = self._transform_geometry(geometry, source_crs, target_crs)
        rings = self._geometry_points(export_geom)
        if not rings:
            raise ValueError("Не могат да бъдат извлечени точки от геометрията.")
        with open(path, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["Номер", "X", "Y"])
            point_no = 0
            for points in rings:
                for point in points:
                    point_no += 1
                    writer.writerow([point_no, "{:.3f}".format(point.x()), "{:.3f}".format(point.y())])

    def _export_vector(self, geometry, source_crs, path, driver, mode):
        if driver == "KML":
            target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        else:
            target_crs = QgsCoordinateReferenceSystem("EPSG:7801")
        export_geom = self._transform_geometry(geometry, source_crs, target_crs)

        if mode == "contour":
            # Build linework from every exterior ring, including multipart geometries.
            rings = self._geometry_points(export_geom)
            lines = [ring + [ring[0]] for ring in rings if len(ring) >= 2]
            if not lines:
                raise ValueError("Не може да се създаде контур.")
            export_geom = (
                QgsGeometry.fromPolylineXY(lines[0])
                if len(lines) == 1
                else QgsGeometry.fromMultiPolylineXY(lines)
            )
            layer = QgsVectorLayer(
                ("MultiLineString" if len(lines) > 1 else "LineString") + "?crs=" + target_crs.authid(),
                "Контур", "memory"
            )
            out_feature = QgsFeature(layer.fields())
            out_feature.setGeometry(export_geom)
            layer.dataProvider().addFeatures([out_feature])
        elif mode == "points":
            rings = self._geometry_points(export_geom)
            layer = QgsVectorLayer("Point?crs=" + target_crs.authid(), "Точки", "memory")
            layer.dataProvider().addAttributes([QgsField("N", QVariant.Int), QgsField("PART", QVariant.Int)])
            layer.updateFields()
            feats = []
            for part_no, points in enumerate(rings, 1):
                for point_no, point in enumerate(points, 1):
                    out_feature = QgsFeature(layer.fields())
                    out_feature.setGeometry(QgsGeometry.fromPointXY(point))
                    out_feature.setAttributes([point_no, part_no])
                    feats.append(out_feature)
            if not feats:
                raise ValueError("Не могат да се създадат точки.")
            layer.dataProvider().addFeatures(feats)
        else:
            raise ValueError("Непозната операция за експорт.")

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        options.fileEncoding = "UTF-8"
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsProject.instance().transformContext(), options
        )
        # QGIS returns (error code, error message, new filename, new layer name).
        if result and result[0] != QgsVectorFileWriter.WriterError.NoError:
            raise RuntimeError(result[1] or "Грешка при запис на файл.")

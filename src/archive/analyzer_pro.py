import sys
import os
import glob
import tempfile
import shutil
import pandas as pd
import numpy as np
import pyqtgraph as pg

# 7zファイル展開用ライブラリのインポート（未インストールの場合は例外処理で対応）
try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False

# ZIPファイル展開用
import zipfile

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                               QMessageBox, QFormLayout, QLineEdit, QGroupBox,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QListWidget, QListWidgetItem, QSplitter)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

# グラフ重ね描き用のカラーパレット（最大20色、循環）
COLOR_PALETTE = [
    '#e6194B', '#3cb44b', '#ffe119', '#4363d8', '#f58231',
    '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#fabed4',
    '#469990', '#dcbeff', '#9a6324', '#fffac8', '#800000',
    '#aaffc3', '#808000', '#ffd8b1', '#000075', '#a9a9a9'
]

class WindTunnelAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wind Tunnel Data Analyzer Pro (Multi-Dataset Comparison)")
        self.resize(1400, 950)

        # 複数データセットを保持する辞書
        # 構造: { "dataset_name": { "df": pd.DataFrame, "color": str, "visible": bool } }
        self.datasets = {}
        self.color_index = 0

        self.setup_ui()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 左右の画面比率をユーザーが調整できるように QSplitter を使用
        main_splitter = QSplitter(Qt.Horizontal, central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.addWidget(main_splitter)

        # === 左パネル (コントロールとデータ管理) ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setMinimumWidth(420)
        left_panel.setMaximumWidth(600)

        # 1. パラメータ設定
        group_params = QGroupBox("1. Setup")
        form_params = QFormLayout()
        self.input_area = QLineEdit("0.1875")
        form_params.addRow("Sail Area [m²]:", self.input_area)
        group_params.setLayout(form_params)
        left_layout.addWidget(group_params)

        # 2. データインポート
        group_import = QGroupBox("2. Import Wind Tunnel Data")
        vbox_import = QVBoxLayout()
        
        # フォルダ選択ボタン
        self.btn_analyze_dir = QPushButton("📁 Load From Folder")
        self.btn_analyze_dir.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 10px;")
        self.btn_analyze_dir.clicked.connect(self.select_and_analyze_dir)
        vbox_import.addWidget(self.btn_analyze_dir)

        # 圧縮ファイル選択ボタン
        self.btn_analyze_archive = QPushButton("📦 Load From Archive (7z / ZIP)")
        self.btn_analyze_archive.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
        self.btn_analyze_archive.clicked.connect(self.select_and_analyze_archive)
        vbox_import.addWidget(self.btn_analyze_archive)

        self.lbl_status_info = QLabel("Filename rule: ..._[Tare/test]_[Angle].csv\nArchives (.7z, .zip) will be extracted automatically.")
        self.lbl_status_info.setStyleSheet("color: #6c757d; font-size: 11px;")
        vbox_import.addWidget(self.lbl_status_info)

        group_import.setLayout(vbox_import)
        left_layout.addWidget(group_import)

        # 3. 読み込み済みデータセット一覧
        group_datasets = QGroupBox("3. Loaded Datasets (Check to Plot)")
        vbox_datasets = QVBoxLayout()
        
        self.dataset_list = QListWidget()
        # チェックボックスの状態変更や、選択項目の変更イベントを接続
        self.dataset_list.itemChanged.connect(self.on_dataset_visibility_changed)
        self.dataset_list.itemSelectionChanged.connect(self.on_dataset_selection_changed)
        vbox_datasets.addWidget(self.dataset_list)

        hbox_ds_btns = QHBoxLayout()
        self.btn_delete_ds = QPushButton("Delete Selected")
        self.btn_delete_ds.clicked.connect(self.delete_selected_dataset)
        self.btn_clear_all = QPushButton("Clear All")
        self.btn_clear_all.clicked.connect(self.clear_all_datasets)
        hbox_ds_btns.addWidget(self.btn_delete_ds)
        hbox_ds_btns.addWidget(self.btn_clear_all)
        vbox_datasets.addLayout(hbox_ds_btns)

        group_datasets.setLayout(vbox_datasets)
        left_layout.addWidget(group_datasets)

        # 4. 解析結果のテーブル (現在選択されているデータセットの詳細)
        group_table = QGroupBox("4. Selected Dataset Details")
        vbox_table = QVBoxLayout()
        
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Angle", "CL", "CD", "L/D"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        vbox_table.addWidget(self.table)

        self.btn_export = QPushButton("💾 Export Selected Summary CSV")
        self.btn_export.setStyleSheet("background-color: #007bff; color: white; font-weight: bold; padding: 8px;")
        self.btn_export.clicked.connect(self.export_csv)
        vbox_table.addWidget(self.btn_export)

        group_table.setLayout(vbox_table)
        left_layout.addWidget(group_table)

        main_splitter.addWidget(left_panel)

        # === 右パネル (グラフ重ね描き群) ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # pyqtgraphテーマ設定（白背景・黒文字）
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        pg.setConfigOption('antialias', True)

        # グラフ1: CL vs Angle
        self.plot_cl = pg.PlotWidget(title="Lift Curve (CL vs Angle)")
        self.plot_cl.showGrid(x=True, y=True)
        self.plot_cl.setLabel('bottom', 'Angle [deg]')
        self.plot_cl.setLabel('left', 'Lift Coefficient (CL)')
        self.plot_cl.addLegend()
        right_layout.addWidget(self.plot_cl)

        # グラフ2: CD vs Angle
        self.plot_cd = pg.PlotWidget(title="Drag Curve (CD vs Angle)")
        self.plot_cd.showGrid(x=True, y=True)
        self.plot_cd.setLabel('bottom', 'Angle [deg]')
        self.plot_cd.setLabel('left', 'Drag Coefficient (CD)')
        self.plot_cd.addLegend()
        right_layout.addWidget(self.plot_cd)

        # グラフ3: Polar Curve
        self.plot_polar = pg.PlotWidget(title="Drag Polar (CL vs CD)")
        self.plot_polar.showGrid(x=True, y=True)
        self.plot_polar.setLabel('bottom', 'Drag Coefficient (CD)')
        self.plot_polar.setLabel('left', 'Lift Coefficient (CL)')
        self.plot_polar.addLegend()
        right_layout.addWidget(self.plot_polar)

        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([450, 950]) # 初期配分設定

        # 描画用のアクティブなカーブオブジェクトを保持する辞書
        # { "dataset_name": { "cl": CurveItem, "cd": CurveItem, "polar": CurveItem } }
        self.plot_curves = {}

    # --- CSV読み込みヘルパー関数 ---
    def _read_csv_safe(self, filepath):
        try:
            return pd.read_csv(filepath, comment='#', encoding='utf-8')
        except UnicodeDecodeError:
            return pd.read_csv(filepath, comment='#', encoding='cp932')

    def _clean_dataframe_columns(self, df):
        df.columns = df.columns.str.strip()
        df.columns = df.columns.str.strip("'")
        df.columns = df.columns.str.strip('"')
        return df

    def _get_mean_values(self, filepath):
        df = self._read_csv_safe(filepath)
        df = self._clean_dataframe_columns(df)
        
        forces = ['Fx(N)', 'Fy(N)', 'Fz(N)', 'Temperature', 'Humidity', 'Pressure', 'AvgWind']
        missing = [col for col in forces if col not in df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        for col in forces:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df = df.dropna(subset=forces)
        if len(df) == 0:
            raise ValueError("No valid numeric data found in CSV.")

        return df[forces].mean()

    # --- 解析コアルーチン ---
    def process_csv_files(self, csv_files, dataset_name):
        """指定されたCSVファイルリストから風洞解析データセットを生成する"""
        try:
            area = float(self.input_area.text())
        except ValueError:
            QMessageBox.warning(self, "Warning", "Invalid Sail Area value.")
            return False

        # 角度ごとにTareとTestをマッピング
        pair_map = {}
        for filepath in csv_files:
            filename = os.path.basename(filepath)
            name_we, _ = os.path.splitext(filename)
            parts = name_we.split('_')
            
            if len(parts) < 3:
                continue 
            
            try:
                angle_str = parts[-1]
                scene_str = parts[-2].lower()
                angle = float(angle_str)
                
                if 'tare' in scene_str:
                    scene = 'tare'
                elif 'test' in scene_str:
                    scene = 'test'
                else:
                    continue
                
                if angle not in pair_map:
                    pair_map[angle] = {'tare': None, 'test': None}
                pair_map[angle][scene] = filepath
                
            except ValueError:
                continue

        new_rows = []
        skipped_angles = []

        for angle, pairs in pair_map.items():
            tare_path = pairs['tare']
            test_path = pairs['test']
            
            if not tare_path or not test_path:
                skipped_angles.append(angle)
                continue
                
            try:
                tare_means = self._get_mean_values(tare_path)
                test_means = self._get_mean_values(test_path)

                # 力の差分(Tare引き)
                net_fx = test_means['Fx(N)'] - tare_means['Fx(N)']
                net_fy = test_means['Fy(N)'] - tare_means['Fy(N)']

                # 動圧の計算
                Tk = test_means['Temperature'] + 273.15
                Es = 6.1078 * 10.0 ** ((7.5 * test_means['Temperature']) / (test_means['Temperature'] + 237.3))
                Pv = Es * (test_means['Humidity'] / 100.0)
                Pd = test_means['Pressure'] - Pv
                rho = (Pd * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
                
                q = 0.5 * rho * (test_means['AvgWind'] ** 2)

                if q < 0.5:
                    continue # 風速不足時はスキップ

                # 係数の計算
                cl = net_fy / (q * area)
                cd = net_fx / (q * area)
                l_d = cl / cd if cd != 0 else 0

                new_rows.append({
                    'Angle': angle, 'CL': cl, 'CD': cd, 'L_D': l_d, 
                    'AvgWind': test_means['AvgWind'], 'AvgRho': rho,
                    'Net_Fy': net_fy, 'Net_Fx': net_fx
                })
            except Exception as e:
                print(f"Failed to process angle {angle}: {e}")
                continue

        if not new_rows:
            return False

        # DataFrameの生成とソート
        df = pd.DataFrame(new_rows)
        df = df.astype(float)
        df = df.sort_values(by='Angle').reset_index(drop=True)

        # カラーアサイン
        color = COLOR_PALETTE[self.color_index % len(COLOR_PALETTE)]
        self.color_index += 1

        # 重複するデータセット名がある場合は自動でリネーム
        original_name = dataset_name
        counter = 1
        while dataset_name in self.datasets:
            dataset_name = f"{original_name}_{counter}"
            counter += 1

        # データセット登録
        self.datasets[dataset_name] = {
            'df': df,
            'color': color,
            'visible': True
        }

        # UIのリストに追加
        self.add_dataset_to_list_widget(dataset_name, color)
        return True

    # --- インポートUIアクション ---
    def select_and_analyze_dir(self):
        """通常フォルダからのデータセット追加"""
        dir_path = QFileDialog.getExistingDirectory(self, "Select Data Directory", "")
        if not dir_path:
            return

        csv_files = glob.glob(os.path.join(dir_path, "*.csv"))
        if not csv_files:
            QMessageBox.warning(self, "No Files", "No CSV files found in the selected folder.")
            return

        dataset_name = os.path.basename(os.path.normpath(dir_path))
        if self.process_csv_files(csv_files, dataset_name):
            self.update_plots()
            # 追加したものを自動でアクティブ選択にする
            self.select_dataset_by_name(dataset_name)
        else:
            QMessageBox.warning(self, "No Valid Data", "No valid Tare/Test pairs found in the selected folder.")

    def select_and_analyze_archive(self):
        """7z / ZIP アーカイブからのデータセット追加"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Archive File", "", "Archive Files (*.7z *.zip)"
        )
        if not file_path:
            return

        _, ext = os.path.splitext(file_path.lower())

        if ext == '.7z' and not HAS_PY7ZR:
            QMessageBox.critical(
                self, "py7zr Missing",
                "7zファイルの展開には 'py7zr' ライブラリが必要です。\n\n"
                "ターミナルで以下のコマンドを実行してインストールしてください:\n"
                "pip install py7zr"
            )
            return

        # 一時ディレクトリを作成して解凍
        temp_dir = tempfile.mkdtemp()
        try:
            if ext == '.7z':
                with py7zr.SevenZipFile(file_path, mode='r') as archive:
                    archive.extractall(path=temp_dir)
            elif ext == '.zip':
                with zipfile.ZipFile(file_path, 'r') as archive:
                    archive.extractall(path=temp_dir)

            # 解凍先から再帰的にすべてのCSVを探索
            csv_files = []
            for root, _, filenames in os.walk(temp_dir):
                for filename in filenames:
                    if filename.lower().endswith('.csv'):
                        csv_files.append(os.path.join(root, filename))

            if not csv_files:
                QMessageBox.warning(self, "No Files", "No CSV files found inside the archive.")
                return

            dataset_name = os.path.basename(file_path)
            if self.process_csv_files(csv_files, dataset_name):
                self.update_plots()
                self.select_dataset_by_name(dataset_name)
                QMessageBox.information(self, "Success", f"Successfully loaded dataset from {dataset_name}!")
            else:
                QMessageBox.warning(self, "No Valid Data", "No valid Tare/Test pairs found in the archive.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract archive:\n{e}")
        finally:
            # 一時ディレクトリのクリーンアップ
            shutil.rmtree(temp_dir, ignore_errors=True)

    # --- UIリストウィジェット操作 ---
    def add_dataset_to_list_widget(self, name, color_hex):
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        item.setCheckState(Qt.Checked)
        
        # 色がわかりやすいようにリストの文字、または左側に色のアイコンを置く
        color = QColor(color_hex)
        item.setForeground(color)
        self.dataset_list.addItem(item)

    def select_dataset_by_name(self, name):
        for i in range(self.dataset_list.count()):
            item = self.dataset_list.item(i)
            if item.text() == name:
                self.dataset_list.setCurrentItem(item)
                break

    # --- イベントハンドラ ---
    def on_dataset_visibility_changed(self, item):
        """チェックボックスのON/OFFが切り替わったときの処理"""
        name = item.text()
        if name in self.datasets:
            self.datasets[name]['visible'] = (item.checkState() == Qt.Checked)
            self.update_plots()

    def on_dataset_selection_changed(self):
        """リストで選択されたデータセットの切り替え時、テーブルを更新する"""
        selected_items = self.dataset_list.selectedItems()
        if not selected_items:
            self.table.setRowCount(0)
            return

        name = selected_items[0].text()
        if name in self.datasets:
            df = self.datasets[name]['df']
            self.update_table(df)

    # --- UI更新 (テーブル / グラフ) ---
    def update_table(self, df):
        self.table.setRowCount(len(df))
        for row, idx in enumerate(df.index):
            self.table.setItem(row, 0, QTableWidgetItem(f"{df.loc[idx, 'Angle']:.1f}"))
            self.table.setItem(row, 1, QTableWidgetItem(f"{df.loc[idx, 'CL']:.4f}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{df.loc[idx, 'CD']:.4f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{df.loc[idx, 'L_D']:.2f}"))

    def update_plots(self):
        """登録されているすべての有効なデータセットを再描画する(重ね描き)"""
        # 一旦描画されているアイテムをすべてクリア
        self.plot_cl.clear()
        self.plot_cd.clear()
        self.plot_polar.clear()

        # 凡例(Legend)の再作成を促すため、クリア後に凡例を再登録
        # pyqtgraphでは、各プロットに対して再度描画を行うことで再構築します
        for name, data in self.datasets.items():
            if not data['visible']:
                continue

            df = data['df']
            color = data['color']
            angles = np.array(df['Angle'].values, dtype=float)
            cls = np.array(df['CL'].values, dtype=float)
            cds = np.array(df['CD'].values, dtype=float)

            # ペンの作成 (線幅2、シンボルあり)
            pen = pg.mkPen(color, width=2)

            # CL vs Angle
            self.plot_cl.plot(
                x=angles, y=cls, name=name, pen=pen,
                symbol='o', symbolBrush=color, symbolSize=6
            )
            
            # CD vs Angle
            self.plot_cd.plot(
                x=angles, y=cds, name=name, pen=pen,
                symbol='o', symbolBrush=color, symbolSize=6
            )

            # Polar (CL vs CD)
            self.plot_polar.plot(
                x=cds, y=cls, name=name, pen=pen,
                symbol='s', symbolBrush=color, symbolSize=6
            )

    # --- 削除・エクスポートアクション ---
    def delete_selected_dataset(self):
        selected_items = self.dataset_list.selectedItems()
        if not selected_items:
            return

        name = selected_items[0].text()
        reply = QMessageBox.question(
            self, "Confirm Delete", f"Are you sure you want to delete dataset '{name}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            # データの削除
            if name in self.datasets:
                del self.datasets[name]
            
            # リストから削除
            for i in range(self.dataset_list.count()):
                if self.dataset_list.item(i).text() == name:
                    self.dataset_list.takeItem(i)
                    break
            
            self.update_plots()
            self.on_dataset_selection_changed()

    def clear_all_datasets(self):
        if not self.datasets:
            return

        reply = QMessageBox.question(
            self, "Clear All", "Are you sure you want to delete all datasets?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.datasets.clear()
            self.dataset_list.clear()
            self.table.setRowCount(0)
            self.update_plots()

    def export_csv(self):
        selected_items = self.dataset_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "Info", "Please select a dataset from the list to export.")
            return

        name = selected_items[0].text()
        if name not in self.datasets:
            return
            
        df = self.datasets[name]['df']
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Summary CSV", f"wind_tunnel_{name}_summary.csv", "CSV Files (*.csv)"
        )
        if filepath:
            try:
                df.to_csv(filepath, index=False)
                QMessageBox.information(self, "Success", "Summary exported successfully!")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export:\n{e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WindTunnelAnalyzer()
    window.show()
    sys.exit(app.exec())
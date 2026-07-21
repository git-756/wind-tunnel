import sys
import os
import csv
import serial
import serial.tools.list_ports
import numpy as np
import pyqtgraph as pg
from datetime import datetime
from collections import deque

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                               QGroupBox, QFormLayout, QMessageBox, QComboBox,
                               QSpinBox, QGridLayout)
from PySide6.QtCore import QThread, Signal, Slot, Qt, QTimer

DEFAULT_SAIL_AREA = 0.1875 # デフォルトのセール面積

CALIB_MATRIX = np.array([
    [0.03259, 0.04063, 0.16369, 12.41458, -0.17227, -12.09472],
    [0.06766, -14.48259, 0.17097, 7.06294, 0.1743, 7.10026],
    [20.94395, 0.10918, 20.82951, 0.1812, 21.38518, -0.04675],
    [-0.00248, -0.07339, 0.29812, 0.03697, -0.29826, 0.03658],
    [-0.33697, -0.00357, 0.17214, -0.05776, 0.17193, 0.05886],
    [0.00147, -0.17701, -0.00125, -0.17339, -0.0026, -0.16894]
])

GRAPH_POINTS = 200 
UI_MA_WINDOW = 10 

BAUD_RATE = 115200
SAVE_DIR = "result_csv"

# ★ 角度の許容誤差を ±0.3deg 以下に設定
FEEDBACK_TOLERANCE_STRICT = 0.20  
FEEDBACK_TOLERANCE_FINAL = 0.45
FEEDBACK_INTERVAL = 3000         
MAX_ITERATIONS = 20              
FEEDBACK_DAMPING = 0.9           

class SerialWorker(QThread):
    data_received = Signal(float, float, float, float, float, object, object, float)
    connection_success = Signal()
    connection_error = Signal(str)

    def __init__(self, port_name):
        super().__init__()
        self.port_name = port_name
        self.is_running = True
        self.is_recording = False
        self.ser = None
        self.csv_file = None
        self.csv_writer = None
        self.sail_area_val = DEFAULT_SAIL_AREA

    def set_recording(self, state, memo="", sail_area="", duration_str="", avg_wind=0.0, is_feedback=False, target_angle=""):
        self.is_recording = state
        if state:
            os.makedirs(SAVE_DIR, exist_ok=True)
            
            try:
                self.sail_area_val = float(sail_area)
            except ValueError:
                self.sail_area_val = DEFAULT_SAIL_AREA
            
            suffix = ""
            if avg_wind <= 0.3:
                suffix = "_Tare"
            elif avg_wind >= 4.0:
                suffix = "_test"
                
            if is_feedback and target_angle:
                suffix += f"_{target_angle}"
                
            filename = datetime.now().strftime('%Y-%m-%d-%H-%M-%S') + suffix + ".csv"
            filepath = os.path.join(SAVE_DIR, filename)
            
            self.csv_file = open(filepath, 'w', newline='')
            self.csv_writer = csv.writer(self.csv_file)
            
            self.csv_writer.writerow(["# --- Wind Tunnel Test Metadata ---"])
            self.csv_writer.writerow(["# Memo:", memo])
            self.csv_writer.writerow(["# Sail Area [m^2]:", self.sail_area_val])
            self.csv_writer.writerow(["# Timer Setting:", duration_str])
            self.csv_writer.writerow([]) 
            
            header = ["Timestamp", "Temperature", "Humidity", "Pressure", 
                      "CH1(V)", "CH2(V)", "CH3(V)", "CH4(V)", "CH5(V)", "CH6(V)", 
                      "Fx(N)", "Fy(N)", "Fz(N)", "F_Total(N)", "Mx(Nm)", "My(Nm)", "Mz(Nm)",
                      "PotDegrees", "AvgWind", "CL", "CD"]
            self.csv_writer.writerow(header)
            print(f"CSV Recording Started: {filename}")
        else:
            if self.csv_file:
                self.csv_file.close()
                self.csv_file = None
                self.csv_writer = None
            print("CSV Recording Stopped")

    def run(self):
        try:
            self.ser = serial.Serial(self.port_name, BAUD_RATE, timeout=1)
            print(f"Connected to {self.port_name}")
            self.connection_success.emit()

            while self.is_running:
                if self.ser.in_waiting > 0:
                    try:
                        line = self.ser.readline().decode('utf-8').strip()
                        if line and "," in line:
                            parts = line.split(',')
                            if len(parts) >= 11:
                                temp, hum, pres = float(parts[0]), float(parts[1]), float(parts[2])
                                pot, wind = float(parts[9]), float(parts[10])
                                
                                sg_array = np.array([float(parts[3]), float(parts[4]), float(parts[5]), 
                                                     float(parts[6]), float(parts[7]), float(parts[8])])
                                
                                fm_array = np.dot(CALIB_MATRIX, sg_array)
                                fx, fy, fz, mx, my, mz = fm_array
                                f_val = np.sqrt(fx**2 + fy**2 + fz**2)

                                self.data_received.emit(temp, hum, pres, pot, wind, sg_array, fm_array, f_val)

                                if self.is_recording and self.csv_writer:
                                    if not np.isnan(temp) and not np.isnan(hum) and not np.isnan(pres):
                                        Tk = temp + 273.15
                                        if Tk > 0:
                                            Es = 6.1078 * 10.0 ** ((7.5 * temp) / (temp + 237.3))
                                            Pv = Es * (hum / 100.0)
                                            Pd = pres - Pv
                                            rho = (Pd * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
                                        else:
                                            rho = np.nan
                                    else:
                                        rho = np.nan
                                    
                                    q = 0.5 * rho * (wind ** 2) if not np.isnan(rho) and not np.isnan(wind) else np.nan
                                    
                                    if not np.isnan(q) and q > 0.5:
                                        cl_csv = fy / (q * self.sail_area_val)
                                        cd_csv = -fx / (q * self.sail_area_val)
                                    else:
                                        cl_csv = 0.0
                                        cd_csv = 0.0

                                    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                                    row = [now_str, temp, hum, pres, 
                                           *sg_array.tolist(), fx, fy, fz, f_val, mx, my, mz, 
                                           pot, wind, cl_csv, cd_csv]
                                    self.csv_writer.writerow(row)
                                    self.csv_file.flush()
                                    
                    except ValueError:
                        pass
                    except Exception as e:
                        print(f"Error reading line: {e}")
                self.msleep(2)

        except Exception as e:
            self.connection_error.emit(str(e))
        finally:
            if self.csv_file:
                self.csv_file.close()
            if self.ser and self.ser.is_open:
                self.ser.close()

    def send_servo_angle(self, angle):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"{angle:.2f}\n".encode('utf-8'))
            except Exception as e:
                print(f"Send Error: {e}")

    def send_relay_command(self, relay_num, state):
        if self.ser and self.ser.is_open:
            try:
                cmd_str = f"R{relay_num}_{'ON' if state else 'OFF'}\n"
                self.ser.write(cmd_str.encode('utf-8'))
                print(f"[Relay Command] Sent: {cmd_str.strip()}")
            except Exception as e:
                print(f"Relay Send Error: {e}")

    def stop(self):
        self.is_running = False
        self.wait()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wind Tunnel Monitor & Aerodynamics Graph")
        self.resize(1400, 950)

        self.time_idx = 0
        self.x_data = deque(maxlen=GRAPH_POINTS)
        self.fx_data = deque(maxlen=GRAPH_POINTS)
        self.fy_data = deque(maxlen=GRAPH_POINTS)
        self.fz_data = deque(maxlen=GRAPH_POINTS)
        self.f_val_data = deque(maxlen=GRAPH_POINTS) 
        self.mx_data = deque(maxlen=GRAPH_POINTS)
        self.my_data = deque(maxlen=GRAPH_POINTS)
        self.mz_data = deque(maxlen=GRAPH_POINTS)
        self.cl_data = deque(maxlen=GRAPH_POINTS) 
        self.cd_data = deque(maxlen=GRAPH_POINTS) 
        
        self.wind_data = deque(maxlen=GRAPH_POINTS)
        self.pot_data = deque(maxlen=GRAPH_POINTS)
        self.temp_data = deque(maxlen=GRAPH_POINTS)
        self.hum_data = deque(maxlen=GRAPH_POINTS)
        self.pres_data = deque(maxlen=GRAPH_POINTS)
        self.rho_data = deque(maxlen=GRAPH_POINTS)
        self.q_data = deque(maxlen=GRAPH_POINTS)
        
        self.sg_data = [deque(maxlen=UI_MA_WINDOW) for _ in range(6)]

        self.pot_offset = 0.0          
        self.fm_offset = np.zeros(6)
        self.latest_fm_raw = np.zeros(6)

        self.last_servo_command = 90.0   
        self.servo_center_val = 90.0     
        self.feedback_target = 0.0
        self.feedback_iteration = 0
        
        self.servo_status = 'READY'
        self.blink_state = False
        self.ui_blink_count = 0

        self.feedback_state = 'ROUGH_MOVING'
        self.rough_move_timeout = 0
        self.rough_stop_count = 0
        self.prev_pot_display = None
        
        self.feedback_timer = QTimer()
        self.feedback_timer.timeout.connect(self.process_feedback_loop)

        # --- 自動計測 (Auto Sweep) 用変数 ---
        self.auto_seq_running = False
        self.auto_angles = []
        self.auto_angle_idx = 0
        self.auto_step_state = 'IDLE'
        self.auto_timer_counter = 0
        
        self.auto_timer = QTimer()
        self.auto_timer.timeout.connect(self.process_auto_sequence)

        self.record_start_time = None
        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self.update_ui_elements)
        self.ui_timer.start(100)

        self.worker = None
        self.setup_ui()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(6)
        left_panel.setFixedWidth(420)

        conn_layout = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_ports()
        conn_layout.addWidget(QLabel("Port:"))
        conn_layout.addWidget(self.port_combo)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self.toggle_connection)
        conn_layout.addWidget(self.btn_connect)
        left_layout.addLayout(conn_layout)

        log_group = QGroupBox("Data Logging")
        log_layout = QVBoxLayout()
        log_layout.setSpacing(4)

        status_layout = QHBoxLayout()
        self.lbl_rec_status = QLabel("Standby")
        self.lbl_rec_status.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        
        self.lbl_elapsed = QLabel("0.0 s")
        self.lbl_elapsed.setStyleSheet("color: white; font-weight: bold; font-size: 16px;")
        self.lbl_elapsed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        status_layout.addWidget(self.lbl_rec_status)
        status_layout.addWidget(self.lbl_elapsed)
        log_layout.addLayout(status_layout)

        timer_layout = QHBoxLayout()
        self.btn_timer_toggle = QPushButton("Timer: OFF")
        self.btn_timer_toggle.setCheckable(True)
        self.btn_timer_toggle.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 3px;")
        self.btn_timer_toggle.clicked.connect(self.toggle_timer_mode)

        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(1, 36000)
        self.spin_duration.setValue(10)       
        self.spin_duration.setSuffix(" s")
        self.spin_duration.setEnabled(False)  

        timer_layout.addWidget(self.btn_timer_toggle)
        timer_layout.addWidget(QLabel("Duration:"))
        timer_layout.addWidget(self.spin_duration)
        log_layout.addLayout(timer_layout)

        memo_layout = QHBoxLayout()
        memo_layout.addWidget(QLabel("Memo:"))
        self.input_memo = QLineEdit()
        self.input_memo.setPlaceholderText("Test description, conditions, etc.")
        memo_layout.addWidget(self.input_memo)
        log_layout.addLayout(memo_layout)

        sail_layout = QHBoxLayout()
        sail_layout.addWidget(QLabel("Sail Area [m^2]:"))
        self.input_sail_area = QLineEdit(str(DEFAULT_SAIL_AREA))
        sail_layout.addWidget(self.input_sail_area)
        log_layout.addLayout(sail_layout)

        self.btn_rec = QPushButton("Start REC (OFF)")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 6px;")
        self.btn_rec.clicked.connect(self.toggle_recording)
        self.btn_rec.setEnabled(False)
        log_layout.addWidget(self.btn_rec)

        log_group.setLayout(log_layout)
        left_layout.addWidget(log_group)

        sensor_group = QGroupBox("Real-time UI (0.5s Avg) / Graph (20Hz Raw)")
        form_layout = QFormLayout()
        form_layout.setVerticalSpacing(4)

        # 環境・風速パラメータ (2列×3行)
        self.lbl_temp = QLabel("--.- °C")
        self.lbl_hum = QLabel("--.- %")
        self.lbl_pres = QLabel("--.- hPa")
        self.lbl_rho = QLabel("--.--- kg/m³")
        self.lbl_q = QLabel("--.- Pa")
        self.lbl_wind = QLabel("--.- m/s")

        env_grid = QGridLayout()
        env_grid.setHorizontalSpacing(8)
        env_grid.setVerticalSpacing(2)

        env_grid.addWidget(QLabel("Temp:"), 0, 0)
        env_grid.addWidget(self.lbl_temp, 0, 1)
        env_grid.addWidget(QLabel("Humidity:"), 0, 2)
        env_grid.addWidget(self.lbl_hum, 0, 3)

        env_grid.addWidget(QLabel("Pressure:"), 1, 0)
        env_grid.addWidget(self.lbl_pres, 1, 1)
        env_grid.addWidget(QLabel("Density(ρ):"), 1, 2)
        env_grid.addWidget(self.lbl_rho, 1, 3)

        env_grid.addWidget(QLabel("Dyn.P(q):"), 2, 0)
        env_grid.addWidget(self.lbl_q, 2, 1)
        env_grid.addWidget(QLabel("Wind:"), 2, 2)
        env_grid.addWidget(self.lbl_wind, 2, 3)

        form_layout.addRow("Env/Wind:", env_grid)

        # Pot / Rotation
        self.lbl_pot = QLabel("--.- deg")
        self.lbl_pot.setStyleSheet("border: 1.5px solid transparent; padding: 1px;")
        self.btn_zero_pot = QPushButton("Set 0°")
        self.btn_zero_pot.clicked.connect(self.set_zero_pot)
        pot_layout = QHBoxLayout()
        pot_layout.addWidget(self.lbl_pot)
        pot_layout.addWidget(self.btn_zero_pot)
        form_layout.addRow("Rotation:", pot_layout)
            
        # 6-Axis Tare
        tare_layout = QHBoxLayout()
        self.btn_tare_toggle = QPushButton("Tare: OFF")
        self.btn_tare_toggle.setCheckable(True)
        self.btn_tare_toggle.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 4px;")
        self.btn_tare_toggle.clicked.connect(self.toggle_tare_force)

        self.btn_tare_update = QPushButton("Update Zero")
        self.btn_tare_update.setStyleSheet("background-color: #ffc107; color: black; font-weight: bold; padding: 4px;")
        self.btn_tare_update.clicked.connect(self.update_tare_force)
        tare_layout.addWidget(self.btn_tare_toggle)
        tare_layout.addWidget(self.btn_tare_update)
        form_layout.addRow("6-Axis Tare:", tare_layout)

        # CH1~6 (2列×3行)
        self.lbl_ch = [QLabel("0.0000 [V]") for _ in range(6)]
        for lbl in self.lbl_ch:
            lbl.setStyleSheet("color: gray;")
            
        ch_grid = QGridLayout()
        ch_grid.setHorizontalSpacing(8)
        ch_grid.setVerticalSpacing(2)
        
        ch_grid.addWidget(QLabel("CH1:"), 0, 0)
        ch_grid.addWidget(self.lbl_ch[0], 0, 1)
        ch_grid.addWidget(QLabel("CH2:"), 0, 2)
        ch_grid.addWidget(self.lbl_ch[1], 0, 3)
        
        ch_grid.addWidget(QLabel("CH3:"), 1, 0)
        ch_grid.addWidget(self.lbl_ch[2], 1, 1)
        ch_grid.addWidget(QLabel("CH4:"), 1, 2)
        ch_grid.addWidget(self.lbl_ch[3], 1, 3)
        
        ch_grid.addWidget(QLabel("CH5:"), 2, 0)
        ch_grid.addWidget(self.lbl_ch[4], 2, 1)
        ch_grid.addWidget(QLabel("CH6:"), 2, 2)
        ch_grid.addWidget(self.lbl_ch[5], 2, 3)
        
        form_layout.addRow("Voltages:", ch_grid)

        # Fx Mx / Fy My / Fz Mz (2列×3行)
        self.lbl_fm = [QLabel("0.0000") for _ in range(6)]
        self.lbl_f_val = QLabel("0.0000")
        
        fm_grid = QGridLayout()
        fm_grid.setHorizontalSpacing(8)
        fm_grid.setVerticalSpacing(2)
        
        fm_grid.addWidget(QLabel("Fx [N]:"), 0, 0)
        fm_grid.addWidget(self.lbl_fm[0], 0, 1)
        fm_grid.addWidget(QLabel("Mx [Nm]:"), 0, 2)
        fm_grid.addWidget(self.lbl_fm[3], 0, 3)
        
        fm_grid.addWidget(QLabel("Fy [N]:"), 1, 0)
        fm_grid.addWidget(self.lbl_fm[1], 1, 1)
        fm_grid.addWidget(QLabel("My [Nm]:"), 1, 2)
        fm_grid.addWidget(self.lbl_fm[4], 1, 3)
        
        fm_grid.addWidget(QLabel("Fz [N]:"), 2, 0)
        fm_grid.addWidget(self.lbl_fm[2], 2, 1)
        fm_grid.addWidget(QLabel("Mz [Nm]:"), 2, 2)
        fm_grid.addWidget(self.lbl_fm[5], 2, 3)

        form_layout.addRow("Loads:", fm_grid)

        # CL / CD横並び
        self.lbl_cl = QLabel("0.000")
        self.lbl_cl.setStyleSheet("color: #d90000; font-weight: bold; font-size: 14px;")
        self.lbl_cd = QLabel("0.000")
        self.lbl_cd.setStyleSheet("color: #0000d9; font-weight: bold; font-size: 14px;")
        
        coeff_layout = QHBoxLayout()
        coeff_layout.addWidget(QLabel("CL:"))
        coeff_layout.addWidget(self.lbl_cl)
        coeff_layout.addWidget(QLabel("CD:"))
        coeff_layout.addWidget(self.lbl_cd)
        form_layout.addRow("Coeffs:", coeff_layout)

        sensor_group.setLayout(form_layout)
        left_layout.addWidget(sensor_group)

        # --- Relay Control Group ---
        relay_group = QGroupBox("Relay Control (G3NA SSR)")
        relay_layout = QHBoxLayout()
        
        self.btn_relay1 = QPushButton("Relay 1: OFF")
        self.btn_relay1.setCheckable(True)
        self.btn_relay1.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 5px;")
        self.btn_relay1.clicked.connect(self.toggle_relay1)
        
        self.btn_relay2 = QPushButton("Relay 2: OFF")
        self.btn_relay2.setCheckable(True)
        self.btn_relay2.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 5px;")
        self.btn_relay2.clicked.connect(self.toggle_relay2)
        
        relay_layout.addWidget(self.btn_relay1)
        relay_layout.addWidget(self.btn_relay2)
        relay_group.setLayout(relay_layout)
        left_layout.addWidget(relay_group)

        # --- Servo Control Group ---
        servo_group = QGroupBox("Servo Control")
        servo_layout = QVBoxLayout()
        servo_layout.setSpacing(4)
        
        base_layout = QHBoxLayout()
        base_layout.addWidget(QLabel("Base Angle:"))
        self.spin_base = QSpinBox()
        self.spin_base.setRange(0, 180)
        self.spin_base.setValue(90)
        self.spin_base.valueChanged.connect(self.update_base_angle)
        base_layout.addWidget(self.spin_base)
        servo_layout.addLayout(base_layout)
        
        mode_layout = QHBoxLayout()
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Direct (0-180)", "Feedback (-90 to +90)"])
        mode_layout.addWidget(QLabel("Mode:"))
        mode_layout.addWidget(self.combo_mode)
        servo_layout.addLayout(mode_layout)
        
        ctrl_layout = QHBoxLayout()
        self.input_angle = QLineEdit()
        self.btn_send = QPushButton("Move")
        self.btn_send.clicked.connect(self.start_servo_control)
        self.btn_center = QPushButton("Center")
        self.btn_center.clicked.connect(self.move_to_center)
        ctrl_layout.addWidget(self.input_angle)
        ctrl_layout.addWidget(self.btn_send)
        ctrl_layout.addWidget(self.btn_center)
        servo_layout.addLayout(ctrl_layout)
        
        servo_group.setLayout(servo_layout)
        left_layout.addWidget(servo_group)

        # --- Auto Sweep Control Group ---
        auto_group = QGroupBox("Auto Measurement Sweep (全自動計測)")
        auto_layout = QVBoxLayout()
        auto_layout.setSpacing(4)
        
        sweep_param_layout = QHBoxLayout()
        self.spin_start_deg = QSpinBox()
        self.spin_start_deg.setRange(-90, 90)
        self.spin_start_deg.setValue(0)
        self.spin_end_deg = QSpinBox()
        self.spin_end_deg.setRange(-90, 90)
        self.spin_end_deg.setValue(60)
        self.spin_step_deg = QSpinBox()
        self.spin_step_deg.setRange(1, 45)
        self.spin_step_deg.setValue(5)
        
        sweep_param_layout.addWidget(QLabel("Start:"))
        sweep_param_layout.addWidget(self.spin_start_deg)
        sweep_param_layout.addWidget(QLabel("End:"))
        sweep_param_layout.addWidget(self.spin_end_deg)
        sweep_param_layout.addWidget(QLabel("Step:"))
        sweep_param_layout.addWidget(self.spin_step_deg)
        auto_layout.addLayout(sweep_param_layout)

        time_param_layout = QHBoxLayout()
        self.spin_rec_dur = QSpinBox()
        self.spin_rec_dur.setRange(1, 60)
        self.spin_rec_dur.setValue(10)
        self.spin_rec_dur.setSuffix(" s")
        
        self.spin_wind_wait = QSpinBox()
        self.spin_wind_wait.setRange(1, 30)
        self.spin_wind_wait.setValue(5)
        self.spin_wind_wait.setSuffix(" s")
        
        time_param_layout.addWidget(QLabel("Rec Dur:"))
        time_param_layout.addWidget(self.spin_rec_dur)
        time_param_layout.addWidget(QLabel("Wind Wait:"))
        time_param_layout.addWidget(self.spin_wind_wait)
        auto_layout.addLayout(time_param_layout)

        self.btn_auto_sweep = QPushButton("START AUTO SWEEP (0°~60°)")
        self.btn_auto_sweep.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 6px;")
        self.btn_auto_sweep.clicked.connect(self.toggle_auto_sweep)
        self.btn_auto_sweep.setEnabled(False)
        auto_layout.addWidget(self.btn_auto_sweep)

        self.lbl_auto_status = QLabel("Auto Sweep: Idle")
        self.lbl_auto_status.setStyleSheet("color: gray; font-weight: bold;")
        auto_layout.addWidget(self.lbl_auto_status)

        auto_group.setLayout(auto_layout)
        left_layout.addWidget(auto_group)

        left_layout.addStretch()

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        pg.setConfigOptions(antialias=True)
        
        self.plot_f = pg.PlotWidget(title="Force [N] (Raw Data)")
        self.plot_f.addLegend()
        self.plot_f.showGrid(x=True, y=True)
        self.curve_fx = self.plot_f.plot(pen=pg.mkPen('r', width=1.5), name="Fx")
        self.curve_fy = self.plot_f.plot(pen=pg.mkPen('g', width=1.5), name="Fy")
        self.curve_fz = self.plot_f.plot(pen=pg.mkPen('b', width=1.5), name="Fz")
        right_layout.addWidget(self.plot_f)

        self.plot_m = pg.PlotWidget(title="Moment [N·m] (Raw Data)")
        self.plot_m.addLegend()
        self.plot_m.showGrid(x=True, y=True)
        self.curve_mx = self.plot_m.plot(pen=pg.mkPen('c', width=1.5), name="Mx")
        self.curve_my = self.plot_m.plot(pen=pg.mkPen('m', width=1.5), name="My")
        self.curve_mz = self.plot_m.plot(pen=pg.mkPen('w', width=1.5), name="Mz")
        right_layout.addWidget(self.plot_m)

        self.plot_coeff = pg.PlotWidget(title="Aerodynamic Coefficients (Raw Data)")
        self.plot_coeff.addLegend()
        self.plot_coeff.showGrid(x=True, y=True)
        self.curve_cl = self.plot_coeff.plot(pen=pg.mkPen((255, 50, 50), width=2), name="CL")
        self.curve_cd = self.plot_coeff.plot(pen=pg.mkPen((50, 100, 255), width=2), name="CD")
        right_layout.addWidget(self.plot_coeff)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel, stretch=1)

    def toggle_relay1(self, checked):
        if checked:
            self.btn_relay1.setText("Relay 1: ON")
            self.btn_relay1.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 5px;")
        else:
            self.btn_relay1.setText("Relay 1: OFF")
            self.btn_relay1.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 5px;")
        if self.worker:
            self.worker.send_relay_command(1, checked)

    def toggle_relay2(self, checked):
        if checked:
            self.btn_relay2.setText("Relay 2: ON")
            self.btn_relay2.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 5px;")
        else:
            self.btn_relay2.setText("Relay 2: OFF")
            self.btn_relay2.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 5px;")
        if self.worker:
            self.worker.send_relay_command(2, checked)

    def toggle_auto_sweep(self):
        if not self.auto_seq_running:
            start_deg = self.spin_start_deg.value()
            end_deg = self.spin_end_deg.value()
            step_deg = self.spin_step_deg.value()
            
            self.auto_angles = list(range(start_deg, end_deg + 1, step_deg))
            if len(self.auto_angles) == 0:
                QMessageBox.warning(self, "Warning", "Invalid angle range!")
                return
            
            self.auto_seq_running = True
            self.auto_angle_idx = 0
            self.auto_step_state = 'MOVE_SERVO'
            self.btn_auto_sweep.setText("STOP AUTO SWEEP")
            self.btn_auto_sweep.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold; padding: 6px;")
            self.combo_mode.setCurrentIndex(1)
            
            self.auto_timer.start(500)
        else:
            self.stop_auto_sweep("Auto Sweep Interrupted by User")

    def stop_auto_sweep(self, msg="Finished"):
        self.auto_seq_running = False
        self.auto_timer.stop()
        if self.btn_relay1.isChecked():
            self.btn_relay1.setChecked(False)
            self.toggle_relay1(False)
        if self.btn_relay2.isChecked():
            self.btn_relay2.setChecked(False)
            self.toggle_relay2(False)
        if self.btn_rec.isChecked():
            self.btn_rec.setChecked(False)
            self.toggle_recording(False)
            
        self.btn_auto_sweep.setText("START AUTO SWEEP")
        self.btn_auto_sweep.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 6px;")
        self.lbl_auto_status.setText(f"Auto Sweep: {msg}")
        self.lbl_auto_status.setStyleSheet("color: blue; font-weight: bold;" if "Done" in msg else "color: red; font-weight: bold;")

    # ★ リレー1 ➔ 5秒待機 ➔ リレー2 の連続シーケンスを実装
    def process_auto_sequence(self):
        if not self.auto_seq_running or self.worker is None:
            return

        target_angle = self.auto_angles[self.auto_angle_idx]
        rec_dur = self.spin_rec_dur.value()
        wind_wait = self.spin_wind_wait.value()

        # 1. サーボの移動要求
        if self.auto_step_state == 'MOVE_SERVO':
            self.lbl_auto_status.setText(f"[{target_angle}°] Moving Servo...")
            self.input_angle.setText(str(target_angle))
            self.start_servo_control()
            self.auto_step_state = 'WAIT_SERVO'
            return

        # 2. サーボ移動完了（±0.30deg内収束）待ち
        elif self.auto_step_state == 'WAIT_SERVO':
            if self.servo_status == 'OK':
                self.lbl_auto_status.setText(f"[{target_angle}°] Servo Settled. Turning Wind OFF for Tare...")
                if self.btn_relay1.isChecked():
                    self.btn_relay1.setChecked(False)
                    self.toggle_relay1(False)
                if self.btn_relay2.isChecked():
                    self.btn_relay2.setChecked(False)
                    self.toggle_relay2(False)
                self.auto_timer_counter = 0
                self.auto_step_state = 'WAIT_WIND_OFF'
            elif self.servo_status == 'ERROR':
                self.stop_auto_sweep(f"Servo Error at {target_angle}°!")
            return

        # 3. 風静止待ち
        elif self.auto_step_state == 'WAIT_WIND_OFF':
            self.auto_timer_counter += 1
            if self.auto_timer_counter >= wind_wait * 2:
                self.lbl_auto_status.setText(f"[{target_angle}°] Recording Tare...")
                self.btn_rec.setChecked(True)
                self.toggle_recording(True)
                self.auto_timer_counter = 0
                self.auto_step_state = 'REC_TARE'
            return

        # 4. Tare計測中
        elif self.auto_step_state == 'REC_TARE':
            self.auto_timer_counter += 1
            if self.auto_timer_counter >= rec_dur * 2:
                self.btn_rec.setChecked(False)
                self.toggle_recording(False)
                
                # Tare完了後、まずリレー1をON
                self.lbl_auto_status.setText(f"[{target_angle}°] Tare Done. Turning Relay 1 ON...")
                self.btn_relay1.setChecked(True)
                self.toggle_relay1(True)
                self.auto_timer_counter = 0
                self.auto_step_state = 'WAIT_RELAY2_ON'
            return

        # ★ 4.5. リレー1 ON から 5秒経過後にリレー2をON
        elif self.auto_step_state == 'WAIT_RELAY2_ON':
            self.auto_timer_counter += 1
            if self.auto_timer_counter >= 5 * 2: # 0.5s * 10 = 5秒待機
                self.lbl_auto_status.setText(f"[{target_angle}°] 5s elapsed. Turning Relay 2 ON...")
                self.btn_relay2.setChecked(True)
                self.toggle_relay2(True)
                self.auto_timer_counter = 0
                self.auto_step_state = 'WAIT_WIND_ON'
            return

        # 5. 送風機ON後の風立ち上がり・定常流待ち
        elif self.auto_step_state == 'WAIT_WIND_ON':
            self.auto_timer_counter += 1
            if self.auto_timer_counter >= wind_wait * 2:
                self.lbl_auto_status.setText(f"[{target_angle}°] Recording Test...")
                self.btn_rec.setChecked(True)
                self.toggle_recording(True)
                self.auto_timer_counter = 0
                self.auto_step_state = 'REC_TEST'
            return

        # 6. Test計測中
        elif self.auto_step_state == 'REC_TEST':
            self.auto_timer_counter += 1
            if self.auto_timer_counter >= rec_dur * 2:
                self.btn_rec.setChecked(False)
                self.toggle_recording(False)
                
                # 両リレーをOFF
                self.btn_relay1.setChecked(False)
                self.toggle_relay1(False)
                self.btn_relay2.setChecked(False)
                self.toggle_relay2(False)
                
                # 次の角度へ進む
                self.auto_angle_idx += 1
                if self.auto_angle_idx < len(self.auto_angles):
                    self.auto_step_state = 'MOVE_SERVO'
                else:
                    self.stop_auto_sweep("Done! All Angles Swept.")
            return

    def toggle_timer_mode(self, checked):
        if checked:
            self.btn_timer_toggle.setText("Timer: ON")
            self.btn_timer_toggle.setStyleSheet("background-color: #0275d8; color: white; font-weight: bold; padding: 5px;")
            self.spin_duration.setEnabled(True)
        else:
            self.btn_timer_toggle.setText("Timer: OFF")
            self.btn_timer_toggle.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 5px;")
            self.spin_duration.setEnabled(False)

    def update_ui_elements(self):
        if self.worker and self.worker.is_recording and self.record_start_time:
            elapsed = (datetime.now() - self.record_start_time).total_seconds()
            self.lbl_elapsed.setText(f"{elapsed:.1f} s")

        if self.servo_status == 'MOVING':
            self.ui_blink_count += 1
            if self.ui_blink_count % 4 == 0:
                self.blink_state = not self.blink_state
                if self.blink_state:
                    self.lbl_pot.setStyleSheet("border: 1.5px solid #ff9800; border-radius: 3px; padding: 1px; font-weight: bold;")
                else:
                    self.lbl_pot.setStyleSheet("border: 1.5px solid transparent; border-radius: 3px; padding: 1px; font-weight: bold;")

        if len(self.fx_data) == 0:
            return

        if len(self.fx_data) >= UI_MA_WINDOW:
            avg_fx = np.nanmean(list(self.fx_data)[-UI_MA_WINDOW:])
            avg_fy = np.nanmean(list(self.fy_data)[-UI_MA_WINDOW:])
            avg_fz = np.nanmean(list(self.fz_data)[-UI_MA_WINDOW:])
            avg_mx = np.nanmean(list(self.mx_data)[-UI_MA_WINDOW:])
            avg_my = np.nanmean(list(self.my_data)[-UI_MA_WINDOW:])
            avg_mz = np.nanmean(list(self.mz_data)[-UI_MA_WINDOW:])
            avg_cl = np.nanmean(list(self.cl_data)[-UI_MA_WINDOW:])
            avg_cd = np.nanmean(list(self.cd_data)[-UI_MA_WINDOW:])
            avg_wind = np.nanmean(list(self.wind_data)[-UI_MA_WINDOW:])
            avg_pot = np.nanmean(list(self.pot_data)[-UI_MA_WINDOW:])
            
            rho_list = list(self.rho_data)
            avg_rho = np.nanmean(rho_list[-UI_MA_WINDOW:]) if len(rho_list) > 0 else np.nan
            q_list = list(self.q_data)
            avg_q = np.nanmean(q_list[-UI_MA_WINDOW:]) if len(q_list) > 0 else np.nan
            
            avg_sg = [np.nanmean(list(self.sg_data[i])) for i in range(6)]
        else:
            avg_fx, avg_fy, avg_fz = self.fx_data[-1], self.fy_data[-1], self.fz_data[-1]
            avg_mx, avg_my, avg_mz = self.mx_data[-1], self.my_data[-1], self.mz_data[-1]
            avg_cl, avg_cd = self.cl_data[-1], self.cd_data[-1]
            avg_wind, avg_pot = self.wind_data[-1], self.pot_data[-1]
            avg_rho, avg_q = self.rho_data[-1], self.q_data[-1]
            avg_sg = [self.sg_data[i][-1] for i in range(6)]

        self.current_pot_display = avg_pot - self.pot_offset

        latest_temp = self.temp_data[-1] if len(self.temp_data)>0 else np.nan
        latest_hum = self.hum_data[-1] if len(self.hum_data)>0 else np.nan
        latest_pres = self.pres_data[-1] if len(self.pres_data)>0 else np.nan

        self.lbl_temp.setText(f"{latest_temp:.2f} °C") 
        self.lbl_hum.setText(f"{latest_hum:.2f} %")
        self.lbl_pres.setText(f"{latest_pres:.2f} hPa")
        self.lbl_rho.setText(f"{avg_rho:.4f} kg/m³")
        self.lbl_q.setText(f"{avg_q:.2f} Pa")
        self.lbl_wind.setText(f"{avg_wind:.2f} m/s")
        self.lbl_pot.setText(f"{self.current_pot_display:+.1f} deg") 
        
        avg_f_val = np.sqrt(avg_fx**2 + avg_fy**2 + avg_fz**2)

        for i in range(6):
            if abs(avg_sg[i]) >= 3.5:
                self.lbl_ch[i].setStyleSheet("color: red; font-weight: bold;")
            else:
                self.lbl_ch[i].setStyleSheet("color: gray;")
            self.lbl_ch[i].setText(f"{avg_sg[i]:.4f} [V]")
            
        self.lbl_fm[0].setText(f"{avg_fx:.4f}")
        self.lbl_fm[1].setText(f"{avg_fy:.4f}")
        self.lbl_fm[2].setText(f"{avg_fz:.4f}")
        self.lbl_fm[3].setText(f"{avg_mx:.4f}")
        self.lbl_fm[4].setText(f"{avg_my:.4f}")
        self.lbl_fm[5].setText(f"{avg_mz:.4f}")
        self.lbl_f_val.setText(f"{avg_f_val:.4f}")
            
        self.lbl_cl.setText(f"{avg_cl:.3f}")
        self.lbl_cd.setText(f"{avg_cd:.3f}")

        x_list = list(self.x_data)
        self.curve_fx.setData(x_list, list(self.fx_data))
        self.curve_fy.setData(x_list, list(self.fy_data))
        self.curve_fz.setData(x_list, list(self.fz_data))
        
        self.curve_mx.setData(x_list, list(self.mx_data))
        self.curve_my.setData(x_list, list(self.my_data))
        self.curve_mz.setData(x_list, list(self.mz_data))

        self.curve_cl.setData(x_list, list(self.cl_data))
        self.curve_cd.setData(x_list, list(self.cd_data))

    def toggle_recording(self, checked):
        if self.worker:
            if checked:
                memo = self.input_memo.text()
                sail_area_str = self.input_sail_area.text() 
                duration_str = f"{self.spin_rec_dur.value()} s" if self.auto_seq_running else "Manual"
                
                avg_wind = 0.0
                if len(self.wind_data) > 0:
                    avg_wind_calc = np.nanmean(list(self.wind_data)[-UI_MA_WINDOW:])
                    if not np.isnan(avg_wind_calc):
                        avg_wind = float(avg_wind_calc)
                
                is_feedback = (self.combo_mode.currentIndex() == 1)
                target_angle = self.input_angle.text()
                
                self.worker.set_recording(True, memo, sail_area_str, duration_str, avg_wind, is_feedback, target_angle)
                self.record_start_time = datetime.now()
                self.lbl_rec_status.setText("● REC")
                self.lbl_rec_status.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
                self.btn_rec.setText("Stop REC (ON)")
                self.btn_rec.setStyleSheet("background-color: #d9534f; color: white; font-weight: bold; padding: 6px;")
            else:
                self.worker.set_recording(False)
                self.record_start_time = None
                self.lbl_rec_status.setText("Standby")
                self.lbl_rec_status.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
                self.lbl_elapsed.setText("0.0 s")
                self.btn_rec.setText("Start REC (OFF)")
                self.btn_rec.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 6px;")

    def toggle_tare_force(self, checked):
        if checked:
            self.btn_tare_toggle.setText("Tare: ON")
            self.btn_tare_toggle.setStyleSheet("background-color: #0275d8; color: white; font-weight: bold; padding: 4px;")
        else:
            self.btn_tare_toggle.setText("Tare: OFF")
            self.btn_tare_toggle.setStyleSheet("background-color: #e0e0e0; color: black; font-weight: bold; padding: 4px;")

    def update_tare_force(self):
        self.fm_offset = np.copy(self.latest_fm_raw)
        if not self.btn_tare_toggle.isChecked():
            self.btn_tare_toggle.setChecked(True)
            self.toggle_tare_force(True)

    def update_base_angle(self, val):
        self.servo_center_val = float(val)
        self.last_servo_command = float(val)

    def refresh_ports(self):
        self.port_combo.clear()
        for p in serial.tools.list_ports.comports():
            self.port_combo.addItem(p.device)

    def toggle_connection(self):
        if self.worker is None:
            port = self.port_combo.currentText()
            if not port: return
            self.worker = SerialWorker(port)
            self.worker.data_received.connect(self.receive_serial_data)
            self.worker.connection_success.connect(self.on_worker_connected)
            self.worker.start()
            self.btn_connect.setText("Disconnect")
            self.btn_rec.setEnabled(True)
            self.btn_auto_sweep.setEnabled(True)
        else:
            if self.auto_seq_running:
                self.stop_auto_sweep("Disconnected")
            self.feedback_timer.stop()
            self.worker.stop()
            self.worker = None
            self.btn_connect.setText("Connect")
            self.btn_rec.setChecked(False)
            self.btn_rec.setEnabled(False)
            self.btn_auto_sweep.setEnabled(False)
            self.lbl_rec_status.setText("Disconnected")
            self.servo_status = 'READY'
            self.lbl_pot.setStyleSheet("border: 1.5px solid transparent; padding: 1px;")

    @Slot()
    def on_worker_connected(self):
        self.send_servo_raw(self.servo_center_val)
        self.btn_zero_pot.setEnabled(True)

    def set_zero_pot(self):
        if len(self.pot_data) > 0:
            self.pot_offset = np.nanmean(list(self.pot_data)[-UI_MA_WINDOW:])
            self.lbl_pot.setText(f"+0.0 deg (Set)")

    @Slot(float, float, float, float, float, object, object, float)
    def receive_serial_data(self, temp, hum, pres, pot, wind, sg_array, fm_array, f_val):
        if not np.isnan(temp): self.temp_data.append(temp)
        if not np.isnan(hum): self.hum_data.append(hum)
        if not np.isnan(pres): self.pres_data.append(pres)
        if not np.isnan(wind): self.wind_data.append(wind)
        if not np.isnan(pot): self.pot_data.append(pot)

        avg_temp = np.mean(self.temp_data) if len(self.temp_data) > 0 else np.nan
        avg_hum = np.mean(self.hum_data) if len(self.hum_data) > 0 else np.nan
        avg_pres = np.mean(self.pres_data) if len(self.pres_data) > 0 else np.nan

        if not np.isnan(avg_temp) and not np.isnan(avg_hum) and not np.isnan(avg_pres):
            Tk = avg_temp + 273.15
            if Tk > 0:
                Es = 6.1078 * 10.0 ** ((7.5 * avg_temp) / (avg_temp + 237.3))
                Pv = Es * (hum / 100.0)
                Pd = avg_pres - Pv
                rho = (Pd * 100.0 / (287.05 * Tk)) + (Pv * 100.0 / (461.495 * Tk))
            else:
                rho = np.nan
        else:
            rho = np.nan
            
        if not np.isnan(rho): self.rho_data.append(rho)

        q = 0.5 * rho * (wind ** 2) if not np.isnan(rho) and not np.isnan(wind) else np.nan
        if not np.isnan(q): self.q_data.append(q)

        self.latest_fm_raw = fm_array
        if self.btn_tare_toggle.isChecked():
            display_fm = fm_array - self.fm_offset
        else:
            display_fm = fm_array

        try:
            sail_area_val = float(self.input_sail_area.text())
        except ValueError:
            sail_area_val = DEFAULT_SAIL_AREA

        if not np.isnan(q) and q > 0.5: 
            cl = display_fm[1] / (q * sail_area_val)
            cd = -display_fm[0] / (q * sail_area_val)
        else:
            cl = 0.0
            cd = 0.0

        for i in range(6):
            self.sg_data[i].append(sg_array[i])
            
        self.time_idx += 1
        self.x_data.append(self.time_idx)
        self.fx_data.append(display_fm[0])
        self.fy_data.append(display_fm[1])
        self.fz_data.append(display_fm[2])
        self.mx_data.append(display_fm[3])
        self.my_data.append(display_fm[4])
        self.mz_data.append(display_fm[5])
        self.cl_data.append(cl)
        self.cd_data.append(cd)

    def send_servo_raw(self, angle_val):
        if self.worker:
            clamped_val = max(0.0, min(180.0, float(angle_val)))
            self.worker.send_servo_angle(clamped_val)
            self.last_servo_command = clamped_val

    def move_to_center(self):
        self.send_servo_raw(self.servo_center_val)
        self.servo_status = 'READY'
        self.lbl_pot.setStyleSheet("border: 1.5px solid transparent; padding: 1px;")

    def start_servo_control(self):
        if self.worker is None: return
        self.feedback_timer.stop()
        
        try:
            val = float(self.input_angle.text())
            if self.combo_mode.currentIndex() == 0:
                self.send_servo_raw(val)
                self.servo_status = 'READY'
                self.lbl_pot.setStyleSheet("border: 1.5px solid transparent; padding: 1px;")
            else:
                self.feedback_target = val
                self.feedback_iteration = 0
                self.rough_move_timeout = 0
                self.rough_stop_count = 0
                self.prev_pot_display = None
                self.feedback_state = 'ROUGH_MOVING'
                
                self.servo_status = 'MOVING'
                self.ui_blink_count = 0
                self.blink_state = True
                self.lbl_pot.setStyleSheet("border: 1.5px solid #ff9800; border-radius: 3px; padding: 1px; font-weight: bold;")
                
                self.send_servo_raw(self.servo_center_val + val)
                self.feedback_timer.start(500) 
        except ValueError:
            self.servo_status = 'ERROR'
            self.lbl_pot.setStyleSheet("border: 1.5px solid #dc3545; border-radius: 3px; padding: 1px; font-weight: bold;")

    def process_feedback_loop(self):
        error = self.feedback_target - self.current_pot_display

        if self.feedback_state == 'ROUGH_MOVING':
            self.rough_move_timeout += 1
            
            if self.prev_pot_display is not None:
                delta = abs(self.current_pot_display - self.prev_pot_display)
                if delta < 0.2:
                    self.rough_stop_count += 1
                else:
                    self.rough_stop_count = 0
            self.prev_pot_display = self.current_pot_display

            if abs(error) <= 2.0 or self.rough_stop_count >= 2:
                self.feedback_state = 'ADJUSTING'
                self.feedback_iteration = 0
                self.feedback_timer.setInterval(FEEDBACK_INTERVAL) 
                print(f"[Servo Log] Rough move done. Starting fine adjustment (Diff: {error:.2f} deg)")
                
                new_command = self.last_servo_command + (error * FEEDBACK_DAMPING)
                self.send_servo_raw(new_command)
                return

            if self.rough_move_timeout > 120: 
                self.finish_feedback(f"[Servo Log] Rough move timeout! (Diff: {error:.2f} deg)", success=False)
            else:
                print(f"[Servo Log] Rough moving... (Diff: {error:.2f} deg)")
            return

        elif self.feedback_state == 'WAITING':
            if abs(error) <= FEEDBACK_TOLERANCE_FINAL:
                self.finish_feedback(f"[Servo Log] Target Reached! [ OK ] (Diff: {error:.2f} deg)", success=True)
                return
            else:
                self.feedback_state = 'ADJUSTING'
                self.feedback_timer.setInterval(FEEDBACK_INTERVAL)
                print(f"[Servo Log] Verification failed. Readjusting... (Diff: {error:.2f} deg)")
                
                new_command = self.last_servo_command + (error * FEEDBACK_DAMPING)
                self.send_servo_raw(new_command)
                return

        if self.feedback_state == 'ADJUSTING':
            self.feedback_iteration += 1
            
            if abs(error) <= FEEDBACK_TOLERANCE_STRICT:
                self.feedback_state = 'WAITING'
                print(f"[Servo Log] Almost there... Waiting 1s to verify (Diff: {error:.2f} deg)")
                self.feedback_timer.setInterval(1000) 
                return

            if self.feedback_iteration >= MAX_ITERATIONS:
                self.finish_feedback(f"[Servo Log] Timeout. (Diff: {error:.2f} deg)", success=False)
                return

            new_command = self.last_servo_command + (error * FEEDBACK_DAMPING)
            self.send_servo_raw(new_command)
            print(f"[Servo Log] Adjusting... Iter:{self.feedback_iteration} Error:{error:.2f} deg")

    def finish_feedback(self, msg, success=False):
        self.feedback_timer.stop()
        print(msg)
        
        if success:
            self.servo_status = 'OK'
            self.lbl_pot.setStyleSheet("border: 1.5px solid #28a745; border-radius: 3px; padding: 1px; font-weight: bold;")
        else:
            self.servo_status = 'ERROR'
            self.lbl_pot.setStyleSheet("border: 1.5px solid #dc3545; border-radius: 3px; padding: 1px; font-weight: bold;")

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
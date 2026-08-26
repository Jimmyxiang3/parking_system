import paho.mqtt.client as mqtt
from models import db, ParkingSpot, EventLog, Device, EnergyRecord
from datetime import datetime
import json

# MQTT配置（后面和上游对齐后改这里就行）
MQTT_BROKER = "localhost"  # MQTT服务器地址，问上游要
MQTT_PORT = 1883
MQTT_TOPICS = [
    "parking/spot/status",      # 车位状态更新
    "parking/device/heartbeat", # 设备心跳
    "parking/energy/report",    # 能耗数据上报
    "parking/event/alert"       # 事件告警
]

# 全局变量，用来存flask app上下文
flask_app = None

def init_mqtt(app):
    """初始化MQTT客户端，在app.py里调用"""
    global flask_app
    flask_app = app
    
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        # 后台运行，不阻塞主程序
        client.loop_start()
        print("✅ MQTT客户端启动成功")
    except Exception as e:
        print(f"⚠️ MQTT连接失败（先忽略，联调时再连）: {e}")
    
    return client

def on_connect(client, userdata, flags, rc):
    """连接成功后订阅所有主题"""
    if rc == 0:
        print("✅ MQTT已连接，开始订阅主题")
        for topic in MQTT_TOPICS:
            client.subscribe(topic)
            print(f"  - 订阅: {topic}")
    else:
        print(f"❌ MQTT连接失败，错误码: {rc}")

def on_message(client, userdata, msg):
    """收到消息时处理"""
    global flask_app
    
    try:
        payload = json.loads(msg.payload.decode())
        topic = msg.topic
        
        with flask_app.app_context():
            # 根据不同主题处理不同数据
            if topic == "parking/spot/status":
                handle_spot_status(payload)
            elif topic == "parking/device/heartbeat":
                handle_device_heartbeat(payload)
            elif topic == "parking/energy/report":
                handle_energy_report(payload)
            elif topic == "parking/event/alert":
                handle_event_alert(payload)
            
            print(f"📥 收到MQTT消息 [{topic}]: {payload}")
            
    except Exception as e:
        print(f"❌ 处理MQTT消息出错: {e}")

# ========== 各个主题的处理函数 ==========

def handle_spot_status(data):
    """处理车位状态更新"""
    spot_code = data.get('spot_code')
    status = data.get('status')  # idle / occupied
    
    spot = ParkingSpot.query.filter_by(spot_code=spot_code).first()
    if spot:
        spot.status = status
        spot.last_updated = datetime.now()
        db.session.commit()

def handle_device_heartbeat(data):
    """处理设备心跳"""
    device_code = data.get('device_code')
    
    device = Device.query.filter_by(device_code=device_code).first()
    if device:
        device.last_heartbeat = datetime.now()
        device.status = 'online'
        db.session.commit()

def handle_energy_report(data):
    """处理能耗数据上报"""
    new_record = EnergyRecord(
        floor=data.get('floor', 1),
        power_usage=data.get('power_usage', 0.0)
    )
    db.session.add(new_record)
    db.session.commit()

def handle_event_alert(data):
    """处理事件告警"""
    new_event = EventLog(
        event_type=data.get('event_type', 'alert'),
        floor=data.get('floor', 1),
        description=data.get('description', '')
    )
    db.session.add(new_event)
    db.session.commit()
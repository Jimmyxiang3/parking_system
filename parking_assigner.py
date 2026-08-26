"""车位分配器 — 用户画像感知 + 车辆分级 + 高峰策略 + A*寻路"""
from models import db, Vehicle, ParkingSpot, ParkingAssignment, ParkingRecord
from services.user_profile_service import UserProfileService
from parking_graph import build_graph_from_db, ParkingGraph
from stereo_astar import astar_search, find_nearest_free_spot
from datetime import datetime


# 全局路网图缓存
_graph_cache = None

def get_parking_graph():
    """获取路网图（带缓存，数据库变化时重建）"""
    global _graph_cache
    if _graph_cache is None:
        _graph_cache = build_graph_from_db()
    return _graph_cache

def refresh_graph():
    """强制重建路网图"""
    global _graph_cache
    _graph_cache = build_graph_from_db()
    return _graph_cache


class ParkingAssigner:
    # 特殊车辆类型（紧急车辆）
    EMERGENCY_TYPES = {'ambulance', 'fire_truck', 'police_car'}

    @staticmethod
    def assign_spots(plate_number, vehicle_type='normal', is_electric=False,
                     need_charging=False, charge_mode='slow', vehicle_size='medium',
                     preferred_entrance=None, use_astar=True):
        """车辆入场，分配2个备选车位（增强版 + A*寻路）"""
        # 1. 加载或创建用户画像
        profile = UserProfileService.get_or_create_profile(plate_number)
        UserProfileService.classify_user(plate_number)

        # 2. 查找或创建车辆
        vehicle = Vehicle.query.filter_by(plate_number=plate_number).first()
        if not vehicle:
            vehicle = Vehicle(
                plate_number=plate_number,
                vehicle_type=vehicle_type,
                vehicle_size=vehicle_size,
                is_electric=is_electric,
                entry_time=datetime.now(),
                user_type=profile.user_type,
                status='inside'
            )
            db.session.add(vehicle)
            db.session.flush()
        else:
            vehicle.entry_time = datetime.now()
            vehicle.status = 'inside'
            vehicle.vehicle_size = vehicle_size
            vehicle.user_type = profile.user_type

        # 3. 判断是否高峰期
        now = datetime.now()
        is_peak = ParkingAssigner._is_peak_hour(now)

        # 4. 筛选条件
        max_floor = None  # 无限制
        only_charging = need_charging
        only_special = (vehicle_type == 'special')
        is_emergency = (vehicle_type in ParkingAssigner.EMERGENCY_TYPES)

        # 5. A* 寻路找最优车位
        if use_astar:
            graph = get_parking_graph()
            occupied = [s.spot_code for s in ParkingSpot.query.filter_by(
                status='occupied').all()]

            # 紧急车辆 → 找最近紧急车位
            if is_emergency:
                best, path, cost = find_nearest_free_spot(
                    graph, "F1_GATE_ENTRY",
                    occupied_spots=occupied,
                    max_floor=1,
                    only_special=True
                )
                if best and path:
                    spot_code = graph.nodes[best].spot_code
                    spot = ParkingSpot.query.filter_by(spot_code=spot_code).first()
                    spots = [spot] if spot else []
                else:
                    spots = []
            else:
                # A* 搜索：根据条件找最近车位
                best, path, cost = find_nearest_free_spot(
                    graph, "F1_GATE_ENTRY",
                    occupied_spots=occupied,
                    max_floor=max_floor,
                    only_charging=only_charging,
                    only_special=only_special
                )

                if best and path:
                    spot_code = graph.nodes[best].spot_code
                    spot = ParkingSpot.query.filter_by(spot_code=spot_code).first()
                    spots = [spot] if spot else []
                else:
                    spots = []

            # A* 降级：如果A*没找到，回退到数据库查询
            if not spots:
                use_astar = False

        # 6. 降级：数据库查询
        if not use_astar or 'spots' not in dir() or not spots:
            query = ParkingSpot.query.filter_by(status='idle')

            if is_emergency:
                emergency_spot = ParkingSpot.query.filter_by(
                    status='idle', is_emergency=True, floor=1
                ).first()
                spots = [emergency_spot] if emergency_spot else ParkingSpot.query.filter_by(
                    status='idle', floor=1
                ).order_by(ParkingSpot.spot_code).limit(2).all()
            else:
                if vehicle_size == 'large':
                    query = query.filter_by(spot_size='large')
                if need_charging:
                    query = query.filter_by(is_charging_spot=True)
                if vehicle_type == 'special':
                    query = query.filter_by(is_special=True)
                if is_peak and profile.user_type == 'temp':
                    query = query.filter(ParkingSpot.is_special == False)

                spots = query.order_by(ParkingSpot.floor, ParkingSpot.spot_code).limit(2).all()

        if len(spots) == 0:
            return {'error': '暂无可用车位'}

        assigned_floor = spots[0].floor

        # 7. 创建分配记录
        assignment = ParkingAssignment(
            vehicle_id=vehicle.id,
            plate_number=plate_number,
            need_charging=need_charging,
            charge_mode=charge_mode if need_charging else None,
            assigned_floor=assigned_floor,
            option1_spot_id=spots[0].id if len(spots) > 0 else None,
            option2_spot_id=spots[1].id if len(spots) > 1 else None,
        )
        db.session.add(assignment)

        vehicle.assigned_floor = assigned_floor
        vehicle.assigned_zone = spots[0].zone

        db.session.commit()

        result = {
            'assignment_id': assignment.id,
            'plate_number': plate_number,
            'assigned_floor': assigned_floor,
            'user_type': profile.user_type,
            'is_peak_hour': is_peak,
            'algorithm': 'A*寻路' if use_astar else '数据库查询',
            'options': []
        }

        for i, spot in enumerate(spots):
            option = {
                'option': i + 1,
                'spot_id': spot.id,
                'spot_code': spot.spot_code,
                'floor': spot.floor,
                'zone': spot.zone,
                'spot_type': spot.spot_type,
                'spot_size': spot.spot_size,
                'is_charging_spot': spot.is_charging_spot,
                'is_emergency': spot.is_emergency
            }

            # 计算 A* 路径
            if use_astar and i == 0:
                graph = get_parking_graph()
                # 查找该车位的节点ID
                target_node_id = None
                for nid, node in graph.nodes.items():
                    if node.spot_code == spot.spot_code:
                        target_node_id = nid
                        break
                if target_node_id:
                    path, cost = astar_search(graph, "F1_GATE_ENTRY", target_node_id,
                                              max_floor=max_floor)
                    if path:
                        option['path'] = path
                        option['path_cost'] = round(cost, 2)

            result['options'].append(option)

        return result

    @staticmethod
    def confirm_spot(aid, selected_option=1):
        """车主确认选择车位（增强版：自动创建停车记录）"""
        assignment = ParkingAssignment.query.get(aid)
        if not assignment:
            return {'error': '分配记录不存在'}

        if assignment.status != 'pending':
            return {'error': '该分配已处理'}

        spot_id = (assignment.option1_spot_id if selected_option == 1
                   else assignment.option2_spot_id)

        spot = ParkingSpot.query.get(spot_id)
        if not spot:
            return {'error': '车位不存在'}

        if spot.status != 'idle':
            return {'error': '车位已被占用'}

        # 更新车位状态
        spot.status = 'occupied'
        spot.last_updated = datetime.now()

        # 更新分配记录
        assignment.selected_spot_id = spot_id
        assignment.status = 'confirmed'

        # 更新车辆状态
        vehicle = Vehicle.query.get(assignment.vehicle_id)
        if vehicle:
            vehicle.assigned_floor = spot.floor
            vehicle.assigned_zone = spot.zone
            vehicle.status = 'inside'

        # 创建停车记录
        record = ParkingRecord(
            vehicle_id=assignment.vehicle_id,
            plate_number=assignment.plate_number,
            spot_id=spot_id,
            spot_code=spot.spot_code,
            entry_time=datetime.now(),
            floor=spot.floor,
            zone=spot.zone
        )
        db.session.add(record)

        # 刷新路网缓存（车位状态变了）
        refresh_graph()

        db.session.commit()

        return {
            'assignment_id': assignment.id,
            'plate_number': assignment.plate_number,
            'selected_spot': spot.spot_code,
            'floor': spot.floor,
            'zone': spot.zone,
            'status': 'confirmed',
            'record_id': record.id
        }

    @staticmethod
    def vehicle_exit(plate_number):
        """车辆离场，更新停车记录并计算时长"""
        vehicle = Vehicle.query.filter_by(
            plate_number=plate_number, status='inside'
        ).first()
        if not vehicle:
            return {'error': '车辆不在场内'}

        now = datetime.now()

        # 更新最近一条未完成的停车记录
        record = ParkingRecord.query.filter_by(
            plate_number=plate_number, status='parking'
        ).order_by(ParkingRecord.entry_time.desc()).first()

        if record:
            record.exit_time = now
            record.duration = int(
                (record.exit_time - record.entry_time).total_seconds() / 60
            )
            record.status = 'completed'

        # 释放车位
        if record and record.spot_id:
            spot = ParkingSpot.query.get(record.spot_id)
            if spot:
                spot.status = 'idle'
                spot.last_updated = now

        # 更新车辆状态
        vehicle.exit_time = now
        vehicle.status = 'outside'
        if vehicle.entry_time:
            vehicle.parking_duration = int(
                (now - vehicle.entry_time).total_seconds() / 60
            )

        db.session.commit()

        # 刷新路网缓存
        refresh_graph()

        # 更新用户画像
        UserProfileService.update_profile(plate_number)

        return {
            'plate_number': plate_number,
            'entry_time': vehicle.entry_time.strftime('%Y-%m-%d %H:%M:%S'),
            'exit_time': now.strftime('%Y-%m-%d %H:%M:%S'),
            'duration_minutes': vehicle.parking_duration,
            'record_id': record.id if record else None
        }

    @staticmethod
    def _is_peak_hour(dt):
        """判断是否高峰时段"""
        h = dt.hour
        m = dt.minute
        time_in_minutes = h * 60 + m

        morning_start = 7 * 60 + 30
        morning_end = 10 * 60
        evening_start = 17 * 60 + 30
        evening_end = 20 * 60

        return (morning_start <= time_in_minutes <= morning_end or
                evening_start <= time_in_minutes <= evening_end)

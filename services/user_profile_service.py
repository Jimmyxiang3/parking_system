"""用户画像服务 — 长期/临时用户分类、画像更新、自动标记"""
from models import db, Vehicle, ParkingRecord, UserProfile
from datetime import datetime, timedelta
from sqlalchemy import func


class UserProfileService:

    @staticmethod
    def get_or_create_profile(plate_number):
        """获取或创建用户画像"""
        profile = UserProfile.query.filter_by(plate_number=plate_number).first()
        if not profile:
            profile = UserProfile(
                plate_number=plate_number,
                user_type='temp',
                first_seen=datetime.now()
            )
            db.session.add(profile)
            db.session.commit()
        return profile

    @staticmethod
    def classify_user(plate_number):
        """根据历史记录判断用户类型"""
        profile = UserProfileService.get_or_create_profile(plate_number)

        # 累计停车次数 >= 20 且 最近3月有停车记录 → 长期用户
        three_months_ago = datetime.now() - timedelta(days=90)
        recent_count = ParkingRecord.query.filter(
            ParkingRecord.plate_number == plate_number,
            ParkingRecord.entry_time >= three_months_ago
        ).count()

        if profile.total_parking_count >= 20 and recent_count >= 5:
            profile.user_type = 'long_term'
        elif profile.total_parking_count >= 10:
            profile.user_type = 'long_term'

        db.session.commit()
        return profile

    @staticmethod
    def update_profile(plate_number):
        """每次停车后更新画像数据"""
        profile = UserProfileService.get_or_create_profile(plate_number)
        now = datetime.now()

        # 总停车次数
        profile.total_parking_count = ParkingRecord.query.filter_by(
            plate_number=plate_number
        ).count()

        # 本月停车次数
        month_start = now.replace(day=1, hour=0, minute=0, second=0)
        profile.monthly_parking_count = ParkingRecord.query.filter(
            ParkingRecord.plate_number == plate_number,
            ParkingRecord.entry_time >= month_start
        ).count()

        # 平均停车时长
        avg_result = db.session.query(
            func.avg(ParkingRecord.duration)
        ).filter(
            ParkingRecord.plate_number == plate_number,
            ParkingRecord.duration.isnot(None)
        ).scalar()
        profile.avg_duration = round(avg_result, 1) if avg_result else 0.0

        # 偏好楼层 — 统计最常停的楼层
        floor_stats = db.session.query(
            ParkingRecord.floor,
            func.count(ParkingRecord.id)
        ).filter(
            ParkingRecord.plate_number == plate_number,
            ParkingRecord.floor.isnot(None)
        ).group_by(ParkingRecord.floor).order_by(
            func.count(ParkingRecord.id).desc()
        ).first()
        if floor_stats:
            profile.preferred_floor = floor_stats[0]

        # 最常入场时段
        hour_stats = db.session.query(
            func.extract('hour', ParkingRecord.entry_time),
            func.count(ParkingRecord.id)
        ).filter(
            ParkingRecord.plate_number == plate_number
        ).group_by(func.extract('hour', ParkingRecord.entry_time)).order_by(
            func.count(ParkingRecord.id).desc()
        ).first()
        if hour_stats:
            profile.peak_entry_hour = int(hour_stats[0])

        # 最常出场时段
        exit_stats = db.session.query(
            func.extract('hour', ParkingRecord.exit_time),
            func.count(ParkingRecord.id)
        ).filter(
            ParkingRecord.plate_number == plate_number,
            ParkingRecord.exit_time.isnot(None)
        ).group_by(func.extract('hour', ParkingRecord.exit_time)).order_by(
            func.count(ParkingRecord.id).desc()
        ).first()
        if exit_stats:
            profile.peak_exit_hour = int(exit_stats[0])

        # 连续活跃月数
        profile.active_months = UserProfileService._calc_active_months(plate_number)

        profile.last_seen = now
        profile.update_time = now

        # 重新分类
        UserProfileService.classify_user(plate_number)

        # 自动标记
        UserProfileService.auto_tag_user(plate_number)

        db.session.commit()
        return profile

    @staticmethod
    def auto_tag_user(plate_number):
        """自动标记用户"""
        profile = UserProfile.query.filter_by(plate_number=plate_number).first()
        if not profile:
            return

        now = datetime.now()
        # 连续3个月 (90天) 在早6:00-8:30进场 → "早高峰固定用户"
        three_months_ago = now - timedelta(days=90)
        morning_peak_entries = ParkingRecord.query.filter(
            ParkingRecord.plate_number == plate_number,
            ParkingRecord.entry_time >= three_months_ago,
            func.extract('hour', ParkingRecord.entry_time).between(6, 8)
        ).count()

        tags = []
        if morning_peak_entries >= 30:
            tags.append('早高峰固定用户')

        if profile.total_parking_count >= 50:
            tags.append('高频用户')

        if profile.monthly_parking_count >= 15:
            tags.append('活跃用户')

        profile.user_tag = '，'.join(tags) if tags else None

        # 连续早高峰进场追踪
        profile.consecutive_peak_entry = morning_peak_entries

        db.session.commit()

    @staticmethod
    def cleanup_inactive(months=6):
        """清理连续N月无使用记录的用户画像"""
        cutoff = datetime.now() - timedelta(days=months * 30)
        stale_profiles = UserProfile.query.filter(
            UserProfile.last_seen < cutoff
        ).all()
        cleaned = []
        for p in stale_profiles:
            cleaned.append(p.plate_number)
            profile_data = p.to_dict()
            db.session.delete(p)
        db.session.commit()
        return {'cleaned_count': len(cleaned), 'plates': cleaned}

    @staticmethod
    def upgrade_user(plate_number):
        """临时用户升级为长期用户"""
        profile = UserProfile.query.filter_by(plate_number=plate_number).first()
        if not profile:
            return {'error': '用户不存在'}

        if profile.user_type == 'long_term':
            return {'message': '已是长期用户', 'user_type': 'long_term'}

        # 次数达标自动升级
        if profile.total_parking_count >= 10:
            profile.user_type = 'long_term'
            # 同时更新 Vehicle 表
            vehicle = Vehicle.query.filter_by(plate_number=plate_number).first()
            if vehicle:
                vehicle.user_type = 'long_term'
            db.session.commit()
            return {'message': '升级成功', 'user_type': 'long_term'}

        return {'message': '次数不足，暂不升级', 'current_count': profile.total_parking_count}

    @staticmethod
    def _calc_active_months(plate_number):
        """计算连续活跃月数"""
        records = db.session.query(
            func.strftime('%Y-%m', ParkingRecord.entry_time).label('month')
        ).filter(
            ParkingRecord.plate_number == plate_number
        ).distinct().order_by('month').all()

        if not records:
            return 0

        months = [r[0] for r in records]
        return len(months)

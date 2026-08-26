import math
from typing import List, Dict, Tuple

# ========== 代价参数 ==========
HEIGHT_PENALTY = 2.0
FLOOR_CHANGE_PENALTY = 10.0
BASE_COST_PER_METER = 1.0

# ========== 1. 节点类 ==========
class Node:
    def __init__(self, node_id: str, x: float, y: float, z: float, 
                 floor: int, zone: str = 'A', node_type: str = 'junction', 
                 spot_code: str = None, is_charging: bool = False,
                 is_special: bool = False):
        self.node_id = node_id
        self.x = x
        self.y = y
        self.z = z
        self.floor = floor
        self.zone = zone
        self.node_type = node_type
        self.spot_code = spot_code
        self.is_charging = is_charging
        self.is_special = is_special
    
    def __repr__(self):
        return f"Node({self.node_id}, F{self.floor}-{self.zone})"

# ========== 2. 边类 ==========
class Edge:
    def __init__(self, from_node: str, to_node: str, edge_type: str = 'road'):
        self.from_node = from_node
        self.to_node = to_node
        self.edge_type = edge_type
        self.distance = 0.0
        self.cost = 0.0
    
    def calculate_cost(self, nodes: Dict[str, Node]):
        from_n = nodes[self.from_node]
        to_n = nodes[self.to_node]
        dx = to_n.x - from_n.x
        dy = to_n.y - from_n.y
        plane_dist = math.sqrt(dx*dx + dy*dy)
        dz = to_n.z - from_n.z
        self.distance = math.sqrt(plane_dist*plane_dist + dz*dz)
        
        base_cost = self.distance * BASE_COST_PER_METER
        height_cost = abs(dz) * HEIGHT_PENALTY
        floor_change_cost = FLOOR_CHANGE_PENALTY if from_n.floor != to_n.floor else 0.0
        self.cost = base_cost + height_cost + floor_change_cost
        return self.cost

# ========== 3. 停车场路网图 ==========
class ParkingGraph:
    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self.adjacency: Dict[str, List[Tuple[str, float]]] = {}
    
    def add_node(self, node: Node):
        self.nodes[node.node_id] = node
        if node.node_id not in self.adjacency:
            self.adjacency[node.node_id] = []
    
    def add_edge(self, edge: Edge, bidirectional: bool = True):
        edge.calculate_cost(self.nodes)
        self.edges.append(edge)
        self.adjacency[edge.from_node].append((edge.to_node, edge.cost))
        if bidirectional:
            reverse_edge = Edge(edge.to_node, edge.from_node, edge.edge_type)
            reverse_edge.calculate_cost(self.nodes)
            self.edges.append(reverse_edge)
            self.adjacency[edge.to_node].append((edge.from_node, reverse_edge.cost))
    
    def get_neighbors(self, node_id: str, max_floor: int = None) -> List[Tuple[str, float]]:
        """获取邻居，max_floor限制最高可达楼层（闸机权限）"""
        neighbors = self.adjacency.get(node_id, [])
        if max_floor is None:
            return neighbors
        result = []
        for nid, cost in neighbors:
            node = self.nodes.get(nid)
            if node and node.floor <= max_floor:
                result.append((nid, cost))
        return result
    
    def get_node(self, node_id: str) -> Node:
        return self.nodes.get(node_id)

# ========== 4. 构建7层测试地图 ==========
def build_sample_parking() -> ParkingGraph:
    """
    7层停车场：
    - 1楼：专用车辆层
    - 2、5、6、7楼：普通车辆层
    - 3、4楼：充电层
    每层 A/B/C 三个区域，坡道连通相邻楼层
    """
    graph = ParkingGraph()
    
    # 楼层功能配置
    floor_config = {
        1: {'type': 'special', 'height': 0},
        2: {'type': 'normal', 'height': 3},
        3: {'type': 'charging', 'height': 6},
        4: {'type': 'charging', 'height': 9},
        5: {'type': 'normal', 'height': 12},
        6: {'type': 'normal', 'height': 15},
        7: {'type': 'normal', 'height': 18},
    }
    
    # ===== 构建每一层 =====
    for floor in range(1, 8):
        config = floor_config[floor]
        z = config['height']
        ftype = config['type']
        prefix = f"F{floor}"
        
        # 入口节点（1楼是主入口）
        if floor == 1:
            graph.add_node(Node(f"{prefix}_GATE_ENTRY", 0, 0, z, floor, node_type='gate'))
        
        # A区
        graph.add_node(Node(f"{prefix}_A_J1", 10, 10, z, floor, 'A'))
        graph.add_node(Node(f"{prefix}_A_P1", 15, 10, z, floor, 'A', 'parking', 
                            f"{floor}F-A-001",
                            is_charging=(ftype=='charging'),
                            is_special=(ftype=='special')))
        graph.add_node(Node(f"{prefix}_A_P2", 15, 15, z, floor, 'A', 'parking', 
                            f"{floor}F-A-002",
                            is_charging=(ftype=='charging'),
                            is_special=(ftype=='special')))
        # B区
        graph.add_node(Node(f"{prefix}_B_J1", 10, -10, z, floor, 'B'))
        graph.add_node(Node(f"{prefix}_B_P1", 15, -10, z, floor, 'B', 'parking', 
                            f"{floor}F-B-001",
                            is_charging=(ftype=='charging'),
                            is_special=(ftype=='special')))
        # C区
        graph.add_node(Node(f"{prefix}_C_J1", 20, 0, z, floor, 'C'))
        graph.add_node(Node(f"{prefix}_C_P1", 25, 5, z, floor, 'C', 'parking', 
                            f"{floor}F-C-001",
                            is_charging=(ftype=='charging'),
                            is_special=(ftype=='special')))
        
        # 层内连通
        if floor == 1:
            graph.add_edge(Edge(f"{prefix}_GATE_ENTRY", f"{prefix}_A_J1"))
        graph.add_edge(Edge(f"{prefix}_A_J1", f"{prefix}_B_J1"))
        graph.add_edge(Edge(f"{prefix}_A_J1", f"{prefix}_C_J1"))
        graph.add_edge(Edge(f"{prefix}_A_J1", f"{prefix}_A_P1"))
        graph.add_edge(Edge(f"{prefix}_A_J1", f"{prefix}_A_P2"))
        graph.add_edge(Edge(f"{prefix}_B_J1", f"{prefix}_B_P1"))
        graph.add_edge(Edge(f"{prefix}_C_J1", f"{prefix}_C_P1"))
        
        # 上坡道（除了顶楼）
        if floor < 7:
            graph.add_node(Node(f"{prefix}_RAMP_UP", 30, 0, z, floor, node_type='ramp'))
            graph.add_edge(Edge(f"{prefix}_C_J1", f"{prefix}_RAMP_UP"))
        
        # 下坡道（除了1楼）
        if floor > 1:
            graph.add_node(Node(f"{prefix}_RAMP_DOWN", 30, 0, z, floor, node_type='ramp'))
            # 坡道连通上下层
            prev_floor = floor - 1
            graph.add_edge(Edge(f"F{prev_floor}_RAMP_UP", f"{prefix}_RAMP_DOWN", 'ramp'))
    
    return graph

# ========== 5. 从数据库构建路网图 ==========
def build_graph_from_db(parking_spots=None) -> ParkingGraph:
    """
    从数据库 ParkingSpot 表读取真实车位数据构建路网图。
    如果传入 parking_spots 列表则直接使用，否则从数据库查询。
    """
    graph = ParkingGraph()

    # 如果没有传入数据，从数据库查询
    if parking_spots is None:
        from models import ParkingSpot
        parking_spots = ParkingSpot.query.all()

    # 获取所有楼层
    floors_set = sorted(set(s.floor for s in parking_spots))
    if not floors_set:
        return graph

    max_floor = max(floors_set)

    # 楼下高度映射（每层3米）
    floor_config = {f: {'height': (f - 1) * 3} for f in floors_set}

    # 获取所有区域
    all_zones = list(set(s.zone for s in parking_spots if s.zone))

    # ===== 构建每一层 =====
    for floor in floors_set:
        config = floor_config[floor]
        z = config['height']
        prefix = f"F{floor}"

        # 入口节点（1楼是主入口）
        if floor == 1:
            graph.add_node(Node(f"{prefix}_GATE_ENTRY", 0, 0, z, floor, node_type='gate'))

        # 为每个区域创建路口节点
        zone_junctions = {}
        for zone in all_zones:
            jid = f"{prefix}_{zone}_J1"
            x = 10 + (ord(zone) - ord('A')) * 15
            y = 10
            graph.add_node(Node(jid, x, y, z, floor, zone))
            zone_junctions[zone] = jid

        # 从数据库读取当前楼层的车位
        floor_spots = [s for s in parking_spots if s.floor == floor]
        for i, spot in enumerate(floor_spots):
            zone = spot.zone or 'A'
            pid = f"{prefix}_{zone}_P{spot.id}"
            x = 15 + (ord(zone) - ord('A')) * 15 + (i % 3) * 5
            y = 10 + (i // 3) * 5
            graph.add_node(Node(
                pid, x, y, z, floor, zone, 'parking',
                spot_code=spot.spot_code,
                is_charging=spot.is_charging_spot,
                is_special=spot.is_special
            ))

            # 连接路口到车位
            if zone in zone_junctions:
                graph.add_edge(Edge(zone_junctions[zone], pid))

        # 层内连通各区域
        zone_keys = list(zone_junctions.keys())
        for i in range(len(zone_keys) - 1):
            graph.add_edge(Edge(zone_junctions[zone_keys[i]], zone_junctions[zone_keys[i + 1]]))

        # 1楼入口连通第一个区域
        if floor == 1 and zone_keys:
            graph.add_edge(Edge(f"{prefix}_GATE_ENTRY", zone_junctions[zone_keys[0]]))

        # 上坡道（除了顶楼）
        if floor < max_floor:
            graph.add_node(Node(f"{prefix}_RAMP_UP", 30, 0, z, floor, node_type='ramp'))
            if zone_keys:
                graph.add_edge(Edge(zone_junctions[zone_keys[-1]], f"{prefix}_RAMP_UP"))

        # 下坡道（除了1楼）
        if floor > 1:
            graph.add_node(Node(f"{prefix}_RAMP_DOWN", 30, 0, z, floor, node_type='ramp'))
            prev_floor = floor - 1
            graph.add_edge(Edge(f"F{prev_floor}_RAMP_UP", f"{prefix}_RAMP_DOWN", 'ramp'))

    return graph

# ========== 测试 ==========
if __name__ == '__main__':
    graph = build_sample_parking()
    floor_counts = {}
    zone_counts = {}
    charging_count = 0
    special_count = 0
    
    for nid, node in graph.nodes.items():
        floor_counts[node.floor] = floor_counts.get(node.floor, 0) + 1
        key = f"{node.floor}F-{node.zone}"
        zone_counts[key] = zone_counts.get(key, 0) + 1
        if node.is_charging:
            charging_count += 1
        if node.is_special:
            special_count += 1
    
    floor_type_map = {1: '专用层', 2: '普通层', 3: '充电层', 4: '充电层', 
                      5: '普通层', 6: '普通层', 7: '普通层'}
    
    print(f"✅ 7层立体路网构建完成")
    print(f"   总节点数：{len(graph.nodes)}")
    print(f"   总边数：{len(graph.edges)}")
    print(f"   充电车位：{charging_count} 个")
    print(f"   专用车位：{special_count} 个")
    
    print(f"\n各楼层统计：")
    for f in sorted(floor_counts.keys()):
        print(f"   {f}楼（{floor_type_map[f]}）：{floor_counts[f]} 个节点")
    
    print(f"\n各区域统计（每层ABC）：")
    for key in sorted(zone_counts.keys()):
        print(f"   {key}区：{zone_counts[key]} 个节点")
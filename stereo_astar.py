import heapq
import math
from typing import List, Tuple, Optional
from parking_graph import ParkingGraph, Node

def heuristic(node_a: Node, node_b: Node) -> float:
    dx = node_b.x - node_a.x
    dy = node_b.y - node_a.y
    dz = node_b.z - node_a.z
    return math.sqrt(dx*dx + dy*dy + dz*dz)

def astar_search(graph: ParkingGraph, start_id: str, end_id: str, 
                 max_floor: int = None) -> Tuple[List[str], float]:
    if start_id not in graph.nodes or end_id not in graph.nodes:
        return [], float('inf')
    
    start_node = graph.get_node(start_id)
    end_node = graph.get_node(end_id)
    
    open_heap = []
    heapq.heappush(open_heap, (0, start_id))
    
    g_score = {start_id: 0.0}
    came_from = {}
    closed_set = set()
    
    while open_heap:
        current_f, current_id = heapq.heappop(open_heap)
        
        if current_id == end_id:
            path = []
            while current_id in came_from:
                path.append(current_id)
                current_id = came_from[current_id]
            path.append(start_id)
            path.reverse()
            return path, g_score[end_id]
        
        if current_id in closed_set:
            continue
        closed_set.add(current_id)
        
        neighbors = graph.get_neighbors(current_id, max_floor=max_floor)
        
        for neighbor_id, edge_cost in neighbors:
            if neighbor_id in closed_set:
                continue
            
            tentative_g = g_score[current_id] + edge_cost
            
            if neighbor_id not in g_score or tentative_g < g_score[neighbor_id]:
                came_from[neighbor_id] = current_id
                g_score[neighbor_id] = tentative_g
                neighbor_node = graph.get_node(neighbor_id)
                f_score = tentative_g + heuristic(neighbor_node, end_node)
                heapq.heappush(open_heap, (f_score, neighbor_id))
    
    return [], float('inf')

def find_nearest_free_spot(graph: ParkingGraph, start_id: str, 
                           occupied_spots: List[str] = None,
                           max_floor: int = None,
                           only_charging: bool = False,
                           only_special: bool = False) -> Tuple[Optional[str], List[str], float]:
    if occupied_spots is None:
        occupied_spots = []
    
    best_spot = None
    best_path = []
    best_cost = float('inf')
    
    for node_id, node in graph.nodes.items():
        if node.node_type != 'parking':
            continue
        if node.spot_code in occupied_spots:
            continue
        if max_floor and node.floor > max_floor:
            continue
        if only_charging and not node.is_charging:
            continue
        if only_special and not node.is_special:
            continue
        
        path, cost = astar_search(graph, start_id, node_id, max_floor=max_floor)
        if path and cost < best_cost:
            best_cost = cost
            best_path = path
            best_spot = node_id
    
    return best_spot, best_path, best_cost

if __name__ == '__main__':
    from parking_graph import build_sample_parking
    graph = build_sample_parking()
    
    print("测试1：普通车到2楼（权限2楼）")
    path, cost = astar_search(graph, "F1_GATE_ENTRY", "F2_A_P1", max_floor=2)
    print(f"  路径：{' → '.join(path)}，代价：{cost:.2f}")
    
    print("\n测试2：电车到3楼充电（权限3楼）")
    path, cost = astar_search(graph, "F1_GATE_ENTRY", "F3_A_P1", max_floor=3)
    print(f"  路径：{' → '.join(path)}，代价：{cost:.2f}")
    
    print("\n测试3：权限2楼尝试去3楼（无权限）")
    path, cost = astar_search(graph, "F1_GATE_ENTRY", "F3_A_P1", max_floor=2)
    print(f"  结果：{'无权限' if not path else '到达'}")
    
    print("\n测试4：找最近充电车位（权限4楼）")
    spot, path, cost = find_nearest_free_spot(graph, "F1_GATE_ENTRY", max_floor=4, only_charging=True)
    print(f"  最近：{spot}，代价：{cost:.2f}")
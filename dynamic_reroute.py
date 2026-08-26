from typing import List, Tuple, Set
from parking_graph import ParkingGraph
from stereo_astar import astar_search

class DynamicRerouter:
    def __init__(self, graph: ParkingGraph):
        self.graph = graph
        self.blocked_edges: Set[Tuple[str, str]] = set()
        self.blocked_nodes: Set[str] = set()
    
    def block_edge(self, from_node: str, to_node: str):
        self.blocked_edges.add((from_node, to_node))
        self.blocked_edges.add((to_node, from_node))
    
    def block_node(self, node_id: str):
        self.blocked_nodes.add(node_id)
    
    def clear_blocks(self):
        self.blocked_edges.clear()
        self.blocked_nodes.clear()
    
    def reroute(self, current_pos: str, target: str,
                max_floor: int = None) -> Tuple[List[str], float]:
        # 临时从图中移除被封堵的边
        original_adj = {}
        for (f, t) in self.blocked_edges:
            if f in self.graph.adjacency:
                original_adj[(f, t)] = [e for e in self.graph.adjacency[f] if e[0] == t]
                self.graph.adjacency[f] = [e for e in self.graph.adjacency[f] if e[0] != t]

        # 临时移除封堵的节点
        blocked_nodes_bak = {}
        for nid in self.blocked_nodes:
            if nid in self.graph.adjacency:
                blocked_nodes_bak[nid] = self.graph.adjacency[nid]
                self.graph.adjacency[nid] = []

        result = astar_search(self.graph, current_pos, target, max_floor=max_floor)

        # 恢复
        for (f, t), edges in original_adj.items():
            self.graph.adjacency[f].extend(edges)
        for nid, edges in blocked_nodes_bak.items():
            self.graph.adjacency[nid] = edges

        return result
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

class Priority(IntEnum): #priority of a given stop
    MANDATORY = 0
    WANT_TO_SEE = 1
    OPTIONAL = 2

class RouteVariant(IntEnum):
    TIME_OPTIMIZED = 0
    COST_OPTIMIZED = 1
    BALANCED = 2

@dataclass
class SolverNode:
    id: str
    name: str
    latitude: float
    longitude: float
    duration_in_minutes: int
    time_window_start: int
    time_window_end: int
    Priority: Priority
    drop_penalty: int #higher values = harder to drop, 0 means mandatory
    candidate_group_id: str = ""
    # advanced mode: per-stop ceiling on duration elasticity (absolute minutes).
    # None -> fall back to the default 1.5x of duration_in_minutes.
    max_duration_in_minutes: Optional[int] = None

@dataclass
class CandidateGroup: #a group of candidates nodes, only one will be+ picked from the group to be put into the route
    id: str
    stop_indices: list[int] #list of indices from the passed in nodes list

@dataclass
class RouteEntry: #a solved segment of the solved route
    node_index: int
    arrival_time_in_minutes: int #time to arrive at this node
    departure_time_in_minutes: int #time to leave this node

@dataclass
class SolverInput:
    nodes: list[SolverNode]
    start_index: int
    end_index: int
    day_start_time_in_minutes: int
    day_end_time_in_minutes: int
    budget_in_cents: int
    travel_time_matrix_in_minutes: list[list[int]]
    travel_cost_matrix_in_cents: list[list[int]]
    candidate_groups: list[CandidateGroup] = field(default_factory=list)
    route_variant: RouteVariant = RouteVariant.BALANCED

    ##### maybe unusued #####
    #list of tuples of node indices where the first index must be visited before the second index
    precedences: list[tuple[int, int]] = field(default_factory=list)
    #list of tuples of node indices where the first index must be visited immediately before the second index
    forced_edges: list[tuple[int, int]] = field(default_factory=list)
    excluded_stops: list[int] = field(default_factory=list)

@dataclass
class SolverOutput:
    route: list[RouteEntry] = field(default_factory=list)
    dropped_stops: list[int] = field(default_factory=list) ##hmmm
    total_time_in_minutes: int = 0
    total_cost_in_cents: int = 0
    score: int = 0
    has_solution: bool = False #if true then the route is possible, if false then the route is impossible given the constraints

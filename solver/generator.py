from ortools.sat.python import cp_model
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
    id :str
    name: str
    latitude: float
    longitude: float
    duration_in_minutes: int
    time_window_start: int
    time_window_end: int
    Priority: Priority
    drop_penalty: int #higher values = harder to drop, 0 means mandatory
    candidate_group_id: str = ""


@dataclass
class CandidateGroup: #a group of candidates nodes, only one will be picked from the group to be put into the route
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
    total_travel_time_in_minutes: int = 0
    total_cost_in_cents: int = 0
    score: int = 0
    has_solution: bool = False #if true then the route is possible, if false then the route is impossible given the constraints
    
def generate_route(solver_input: SolverInput) -> SolverOutput:
    model = cp_model.CpModel()
    num_nodes = len(solver_input.nodes)


    ## add edges to the model
    edge = {}

    #filter out some dumb edge possibilities and add viable ones to the model
    for i in range(num_nodes):
        if i == solver_input.end_index: #ignore case where you start at the exit point
            continue
        for j in range(num_nodes):
            if j == solver_input.start_index: #ignore case where you end at the start point
                continue
            if i == j: #ignore case where you have an edge from a node to itself
                continue
            edge [(i,j)] = model.new_bool_var(f"edge_{i}_{j}") #add edge to model


    ## indicate which nodes are droppable
    is_dropped = {} #dictionary of possble nodes to drop

    #iterate over nodes to see which are droppable or not
    for i in range(num_nodes):
        if i in (solver_input.start_index,solver_input.end_index): #can't drop start and end point nodes
            continue
        if solver_input.nodes[i].Priority == Priority.MANDATORY: #if the node is mandatory, then we can't drop it
            continue
        is_dropped[i] = model.new_bool_var(f"is_dropped_{i}") #tell the model which nodes can be dropped


    ## time variables (clock time in minutes)
    ## i.e. 600 means 10:00am, 720 means 12:00pm, etc.
    arrival_time = {}

    for i in range(num_nodes):
        arrival_time.append (
            model.new_int_var(
                solver_input.day_start_time_in_minutes,
                solver_input.day_end_time_in_minutes,
                f"arrival_time_{i}"
            )
        )
        
    ## cost variables (in cents)
    cumulative_cost = []
    for i in range(num_nodes):
        cumulative_cost.append(
            model.new_int_var(
                0, #lower bound
                solver_input.budget_in_cents * 3, #upper bound, x3 is arbirtrary for headroom
                f"cost_{i}"
            )
        )

    ##circuit constraint

    #a single path that goes through all nodes
    arcs = []

    #add actual edges
    for (from_index,to_index), edge_var in edge.items():
        arcs.append((from_index, to_index, edge_var))

    #add dropped nodes as self loops
    #so this for loop makes dummy routes to satisfy the math formula or something like that?
    for index, drop_var in is_dropped.items():
        arcs.append((index, index, drop_var))


    #the way cpmodel works is that it "only" works if the nodes are a roundtrip path
    #so here we fake it by pointing the last node to the start
    dummy_close = model.new_bool_var("dummy_close")
    arcs.append((solver_input.end_index, solver_input.start_index, dummy_close))
    model.add(dummy_close == 1) #force the dummy close edge to be used, this is needed to satisfy the circuit constraint math

    model.add_circuit(arcs) 


    ## start conditions
    model.add(arrival_time[solver_input.start_index] == solver_input.day_start_time_in_minutes) #start at the start node at the start of the day
    model.add(cumulative_cost[solver_input.start_index] == 0) #start with 0 cost


    ## time windows
    for i in range(num_nodes):
        node = solver_input.nodes[i]
        
        #check if a node is mandatory or not
        is_always_visited = (
            i == solver_input.start_index
            or i == solver_input.end_index
            or node.priority == Priority.MANDATORY
        )

        if is_always_visited:
            #if the node is mandatory, then we must arrive within the time window
            model.add(arrival_time[i] >= node.time_window_start)
            model.add(arrival_time[i] <= node.time_window_end)

        elif i in is_dropped:
            # only enforce if node is not dropped
            model.add(arrival_time[i] >= node.time_window_start).only_enforce_if(is_dropped[i].Not())
            model.add(arrival_time[i] <= node.time_window_end).only_enforce_if(is_dropped[i].Not())

    ## time propagation
    # ensures you can't arrive at j before finishing i + traveling
    for (from_index, to_index), edge_var in edge.items():
        
        min_gap = (
            solver_input.nodes[from_index].duration_in_minutes + solver_input.travel_time_matrix_in_minutes[from_index][to_index]
        )
        model.add(arrival_time[to_index] - arrival_time[from_index] >= min_gap).only_enforce_if(edge_var)
        

    
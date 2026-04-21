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
    
def generate_route(solver_input: SolverInput) -> SolverOutput:
    model = cp_model.CpModel()
    num_nodes = len(solver_input.nodes)

    # Handle round-trip: duplicate the start node as a virtual end node
    ## TODO MOVE TO go??
    round_trip = solver_input.start_index == solver_input.end_index
    open_end = solver_input.end_index == -1
    needs_virtual_end = round_trip or open_end


    
    if needs_virtual_end:
        virtual_end = num_nodes
        num_nodes += 1

        #pick a template node to clone metadata off of
        template = solver_input.nodes[solver_input.start_index]
        solver_input.nodes.append(SolverNode(
            id="virtual_end",
            name=template.name,
            latitude=template.latitude,
            longitude=template.longitude,
            duration_in_minutes=0,
            time_window_start=solver_input.day_start_time_in_minutes,
            time_window_end=solver_input.day_end_time_in_minutes,
            Priority=Priority.MANDATORY,
            drop_penalty=0,
        ))

        if round_trip:
            src = solver_input.start_index
            for row in solver_input.travel_time_matrix_in_minutes:
                row.append(row[src])
            for row in solver_input.travel_cost_matrix_in_cents:
                row.append(row[src])
        else:
            #open_end: reaching virtual_end is free from any real node
            for row in solver_input.travel_time_matrix_in_minutes:
                row.append(0)
            for row in solver_input.travel_cost_matrix_in_cents:
                row.append(0)

        #outgoing edges FROM virtual_end are all 0 (it's the terminal; nothing comes after)
        solver_input.travel_time_matrix_in_minutes.append([0] * num_nodes)
        solver_input.travel_cost_matrix_in_cents.append([0] * num_nodes)

        solver_input.end_index = virtual_end


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
    arrival_time = []

    for i in range(num_nodes):
        arrival_time.append (
            model.new_int_var(
                solver_input.day_start_time_in_minutes,
                solver_input.day_end_time_in_minutes,
                f"arrival_time_{i}"
            )
        )


    ##duration variables
    duration = []
    for i in range(num_nodes):
        node = solver_input.nodes[i]
        duration.append(
            model.new_int_var(
                node.duration_in_minutes, # min duration is the actual duration
                int(node.duration_in_minutes* 3 //2 ), ##placeholder
                f"duration_{i}"
            )
        )

    ## duration extension penalty
    duration_ext = []
    for i in range(num_nodes):
        node = solver_input.nodes[i]
        base = node.duration_in_minutes
        max_extension = (base * 3 // 2) - base
        extension = model.new_int_var(0, max_extension, f"duration_ext_{i}")
        model.add(extension == duration[i] - base)

        if i in is_dropped:
            model.add(extension == 0).only_enforce_if(is_dropped[i])

        duration_ext.append(extension)





    ## adapt acvitity start if its an appointment time or not
    activity_start = []
    for i in range(num_nodes):
        node = solver_input.nodes[i]
        #check if this stop has narrowed time windows (an appointment)
        #if so, start the activity at the preferred time (window_end + appt buffer)
        appt_buffer = 5 #minutes after the end of the time window to start the activity
        if node.time_window_end != solver_input.day_end_time_in_minutes:
            a = model.new_int_var(
                node.time_window_end + appt_buffer, node.time_window_end + appt_buffer,
                f"activity_start_{i}"
            )
        else:
            #no appointment: activity starts immediately on arrival
            a = arrival_time[i]
        activity_start.append(a)

        
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

    ## circuit constraint

    # a single path that goes through all nodes
    arcs = []

    # add actual edges
    for (from_index,to_index), edge_var in edge.items():
        arcs.append((from_index, to_index, edge_var))

    # add dropped nodes as self loops
    # so this for loop makes dummy routes to satisfy the math formula or something like that?
    for index, drop_variable in is_dropped.items():
        arcs.append((index, index, drop_variable))


    # the way cpmodel works is that it "only" works if the nodes are a roundtrip path
    # so here we fake it by pointing the last node to the start
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
            or node.Priority == Priority.MANDATORY
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
        travel = solver_input.travel_time_matrix_in_minutes[from_index][to_index]
        model.add(arrival_time[to_index] - activity_start[from_index] - duration[from_index] >= travel).only_enforce_if(edge_var)
        

    ## cost propagation
    # tracks entire transit expenditure along a route

    for (from_index, to_index), edge_var in edge.items():
        leg_cost = solver_input.travel_cost_matrix_in_cents[from_index][to_index]
        model.add(cumulative_cost[to_index] - cumulative_cost[from_index] == leg_cost).only_enforce_if(edge_var)

        # total cost must not exceed budget check
        model.add(cumulative_cost[to_index] <= solver_input.budget_in_cents).only_enforce_if(edge_var)


    ## idle time
    idle_time = {}
    for (from_index, to_index), edge_var in edge.items():
        
        ##its just an upper bound not the actual idle max, each minute of idle is penaltied agaisnt the score
        max_possible_idle = solver_input.day_end_time_in_minutes - solver_input.day_start_time_in_minutes
        idle = model.new_int_var(0, max_possible_idle, f"idle_{from_index}_{to_index}")

        #when edge is active: idle = arrival[j] - arrival[i] - duration[i] - travel[i][j]
        travel = solver_input.travel_time_matrix_in_minutes[from_index][to_index]
        model.add(
            idle == arrival_time[to_index] - activity_start[from_index] - duration[from_index] - travel
        ).only_enforce_if(edge_var)

        #when edge is inactive: idle = 0 
        model.add(idle == 0).only_enforce_if(edge_var.Not())

        idle_time[(from_index, to_index)] = idle




   
    ## candidate groups
    # only one member of each group is picked

    for group in solver_input.candidate_groups:
        visit_variables = []
        for stop_index in group.stop_indices:
            if stop_index in is_dropped:
                visit_variables.append(is_dropped[stop_index].Not()) 
                #if the node is optional, we add it to the candidate group
        if visit_variables:
            model.add_exactly_one(visit_variables)

    ## excluded/deleted stops
    # if a user deletes a stop, we can have the solver treat it as dead
    for stop_index in solver_input.excluded_stops:
        if stop_index in is_dropped:
            model.add(is_dropped[stop_index] == 1)


    ## precedence constraint
    # force one node to occur before the other (but not necessarily immediately before)
    for before_index, after_index in solver_input.precedences:
        gap = solver_input.nodes[before_index].duration_in_minutes
        model.add(arrival_time[after_index] - arrival_time[before_index] >= gap)

    ## forced edges constraint
    # force one node to occur immediately before the other
    for before_index, after_index in solver_input.forced_edges:
        if (before_index, after_index) in edge:
            model.add(edge[(before_index, after_index)] == 1)

    ## objective function
    # defines how a route is scored by the time,cost and drop penalties, the solver will try to minimize this score

    if solver_input.route_variant == RouteVariant.TIME_OPTIMIZED:
        time_w, cost_w, penalty_w = 100, 0, 1000
    elif solver_input.route_variant == RouteVariant.COST_OPTIMIZED:
        time_w, cost_w, penalty_w = 0, 100, 1000
    else:  #BALANCED
        time_w, cost_w, penalty_w = 50, 50, 1000

    objective_terms = []

    #travel time
    if time_w > 0:
        for (from_index, to_index), edge_var in edge.items():
            travel_time = solver_input.travel_time_matrix_in_minutes[from_index][to_index]
            objective_terms.append(edge_var * travel_time * time_w)

    #travel cost
    if cost_w > 0:
        objective_terms.append(cumulative_cost[solver_input.end_index] * cost_w)
        #total cost is the cumulative cost at the end node

    #drop penalties

    for index, drop_variable in is_dropped.items():
        drop_penalty = solver_input.nodes[index].drop_penalty
        if drop_penalty > 0:
            objective_terms.append(drop_variable * drop_penalty * penalty_w)

    #idle penalty
    idle_w = 50  #penalize each minute of dead time
    for (from_index, to_index), idle_var in idle_time.items():
        objective_terms.append(idle_var * idle_w)



    ## duration extension penalty
    duration_ext_w = 30
    for i in range(num_nodes):
        if i in (solver_input.start_index, solver_input.end_index):
            continue
        objective_terms.append(duration_ext[i] * duration_ext_w)





    model.minimize(sum(objective_terms))

    






    #### SOLVER
    solver = cp_model.CpSolver()

    ## MAX RUN TIME
    solver.parameters.max_time_in_seconds = 2.0

    solver.parameters.num_search_workers = 8
    solver.parameters.enumerate_all_solutions = False

    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolverOutput(has_solution=False)
    
    
    return _extract_solution(solver, solver_input, edge, is_dropped, arrival_time,
                         cumulative_cost, duration, needs_virtual_end, 
                          round_trip, activity_start)

    
def _extract_solution (
        solver: cp_model.CpSolver,
        solver_input: SolverInput,
        edge: dict,
        is_dropped: dict,
        arrival_time: list,
        cumulative_cost: list,
        duration: list,
        has_virtual_end: bool = False,
        round_trip: bool = False,
        activity_start: list = None
        
) -> SolverOutput:
    ## goes from start to finish over each active edge to build a route
    num_nodes = len(solver_input.nodes)
    route = []
    current_index = solver_input.start_index
    visited = set()

    while True:

        arrival_time_for_current = solver.Value(arrival_time[current_index])
        activity_start_for_current = solver.Value(activity_start[current_index])
        departure_time_for_current = activity_start_for_current + solver.Value(duration[current_index])
    
        route.append(RouteEntry(
            node_index=current_index,
            arrival_time_in_minutes=arrival_time_for_current,
            departure_time_in_minutes=departure_time_for_current
        ))
        visited.add(current_index)

        if current_index == solver_input.end_index:
            break

        #find next node
        found_next = False
        for j in range(num_nodes):
            if j in visited or j == current_index:
                continue
            if (current_index, j) in edge and solver.Value(edge[(current_index, j)]) == 1:
                current_index = j
                found_next = True
                break

        if not found_next:
            raise Exception("No next node found in solution path")
        
    # collect dropped stops
    dropped = [index for index, drop_variable in is_dropped.items() if solver.Value(drop_variable)]   
   
    if has_virtual_end and route:
        virtual_entry = route.pop()  # remove dummy end node
        if round_trip:
            # Add the real start node back as the final stop to represent returning home
            route.append(RouteEntry(
                node_index=solver_input.start_index,
                arrival_time_in_minutes=virtual_entry.arrival_time_in_minutes,
                departure_time_in_minutes=virtual_entry.arrival_time_in_minutes,
            ))


    # get total travel time and travel cost for the route
    total_Travel_time_in_minutes = 0
    for i in range(1,len(route)):
        prev_index = route[i-1].node_index
        current_index = route[i].node_index
        total_Travel_time_in_minutes += solver_input.travel_time_matrix_in_minutes[prev_index][current_index]

    total_route_cost = solver.Value(cumulative_cost[route[-1].node_index]) #cumulative cost at the end node is the total route cost

    

    return SolverOutput (
        route=route,
        dropped_stops = dropped,
        total_time_in_minutes=total_Travel_time_in_minutes,
        total_cost_in_cents=total_route_cost,
        score= int(solver.objective_value),
        has_solution=True
    )

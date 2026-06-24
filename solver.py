from ortools.sat.python import cp_model
from models import Priority, SolverNode, SolverInput, SolverOutput
from config import SolverConfig
from extraction import _extract_solution


def generate_route(solver_input: SolverInput, config: SolverConfig = None) -> SolverOutput:
    if config is None:
        config = SolverConfig.from_route_variant(solver_input.route_variant)

    model = cp_model.CpModel()
    num_nodes = len(solver_input.nodes)

    num_nodes, needs_virtual_end, round_trip = _add_virtual_end_node(solver_input, num_nodes)

    # set variables
    edge = _build_edges(model, solver_input, num_nodes)
    is_dropped = _build_drop_variables(model, solver_input, num_nodes)
    arrival_time = _build_arrival_time_variables(model, solver_input, num_nodes)
    duration = _build_duration_variables(model, solver_input, num_nodes)
    duration_ext = _build_duration_extension(model, solver_input, num_nodes, duration, is_dropped)
    activity_start = _build_activity_start(model, solver_input, num_nodes, arrival_time)
    cumulative_cost = _build_cumulative_cost_variables(model, solver_input, num_nodes)

    # add constraints
    _add_circuit_constraint(model, solver_input, edge, is_dropped)
    _add_start_conditions(model, solver_input, arrival_time, cumulative_cost)
    _add_time_windows(model, solver_input, num_nodes, arrival_time, is_dropped)
    _add_time_propagation(model, solver_input, edge, arrival_time, activity_start, duration)
    _add_cost_propagation(model, solver_input, edge, cumulative_cost)
    idle_time = _add_idle_time(model, solver_input, edge, arrival_time, activity_start, duration)
    _add_candidate_group_constraints(model, solver_input, is_dropped)
    _add_excluded_stops_constraint(model, solver_input, is_dropped)
    _add_precedence_constraints(model, solver_input, arrival_time)
    _add_forced_edges_constraint(model, solver_input, edge)

    # build model
    objective_terms = _build_objective(
        solver_input, config, edge, cumulative_cost, is_dropped,
        idle_time, duration_ext, num_nodes
    )
    model.minimize(sum(objective_terms))

    solver = _configure_solver(config)
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolverOutput(has_solution=False)

    return _extract_solution(solver, solver_input, edge, is_dropped, arrival_time,
                         cumulative_cost, duration, needs_virtual_end,
                          round_trip, activity_start)


def _add_virtual_end_node(solver_input: SolverInput, num_nodes: int) -> tuple[int, bool, bool]:
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

    return num_nodes, needs_virtual_end, round_trip


def _build_edges(model: cp_model.CpModel, solver_input: SolverInput, num_nodes: int) -> dict:
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

    return edge


def _build_drop_variables(model: cp_model.CpModel, solver_input: SolverInput, num_nodes: int) -> dict:
    ## indicate which nodes are droppable
    is_dropped = {} #dictionary of possble nodes to drop

    #iterate over nodes to see which are droppable or not
    for i in range(num_nodes):
        if i in (solver_input.start_index,solver_input.end_index): #can't drop start and end point nodes
            continue
        if solver_input.nodes[i].Priority == Priority.MANDATORY: #if the node is mandatory, then we can't drop it
            continue
        is_dropped[i] = model.new_bool_var(f"is_dropped_{i}") #tell the model which nodes can be dropped

    return is_dropped


def _build_arrival_time_variables(model: cp_model.CpModel, solver_input: SolverInput, num_nodes: int) -> list:
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

    return arrival_time


def _node_max_duration(node: SolverNode) -> int:
    ## per-stop ceiling on duration elasticity
    # advanced mode: explicit per-stop ceiling; else the 1.5x placeholder default.
    if node.max_duration_in_minutes is not None:
        return max(node.max_duration_in_minutes, node.duration_in_minutes)  # clamp: never below the min
    return node.duration_in_minutes * 3 // 2


def _build_duration_variables(model: cp_model.CpModel, solver_input: SolverInput, num_nodes: int) -> list:
    ##duration variables
    duration = []
    for i in range(num_nodes):
        node = solver_input.nodes[i]
        duration.append(
            model.new_int_var(
                node.duration_in_minutes, # min duration is the actual duration
                _node_max_duration(node),
                f"duration_{i}"
            )
        )

    return duration


def _build_duration_extension(model: cp_model.CpModel, solver_input: SolverInput, num_nodes: int, duration: list, is_dropped: dict) -> list:
    ## duration extension penalty
    duration_ext = []
    for i in range(num_nodes):
        node = solver_input.nodes[i]
        base = node.duration_in_minutes
        max_extension = _node_max_duration(node) - base
        extension = model.new_int_var(0, max_extension, f"duration_ext_{i}")
        model.add(extension == duration[i] - base)

        if i in is_dropped:
            model.add(extension == 0).only_enforce_if(is_dropped[i])

        duration_ext.append(extension)

    return duration_ext


def _build_activity_start(model: cp_model.CpModel, solver_input: SolverInput, num_nodes: int, arrival_time: list) -> list:
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

    return activity_start


def _build_cumulative_cost_variables(model: cp_model.CpModel, solver_input: SolverInput, num_nodes: int) -> list:
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

    return cumulative_cost


def _add_circuit_constraint(model: cp_model.CpModel, solver_input: SolverInput, edge: dict, is_dropped: dict) -> None:
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


def _add_start_conditions(model: cp_model.CpModel, solver_input: SolverInput, arrival_time: list, cumulative_cost: list) -> None:
    ## start conditions
    model.add(arrival_time[solver_input.start_index] == solver_input.day_start_time_in_minutes) #start at the start node at the start of the day
    model.add(cumulative_cost[solver_input.start_index] == 0) #start with 0 cost


def _add_time_windows(model: cp_model.CpModel, solver_input: SolverInput, num_nodes: int, arrival_time: list, is_dropped: dict) -> None:
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


def _add_time_propagation(model: cp_model.CpModel, solver_input: SolverInput, edge: dict, arrival_time: list, activity_start: list, duration: list) -> None:
    ## time propagation
    # ensures you can't arrive at j before finishing i + traveling
    for (from_index, to_index), edge_var in edge.items():
        travel = solver_input.travel_time_matrix_in_minutes[from_index][to_index]
        model.add(arrival_time[to_index] - activity_start[from_index] - duration[from_index] >= travel).only_enforce_if(edge_var)


def _add_cost_propagation(model: cp_model.CpModel, solver_input: SolverInput, edge: dict, cumulative_cost: list) -> None:
    ## cost propagation
    # tracks entire transit expenditure along a route

    for (from_index, to_index), edge_var in edge.items():
        leg_cost = solver_input.travel_cost_matrix_in_cents[from_index][to_index]
        model.add(cumulative_cost[to_index] - cumulative_cost[from_index] == leg_cost).only_enforce_if(edge_var)

        # total cost must not exceed budget check
        model.add(cumulative_cost[to_index] <= solver_input.budget_in_cents).only_enforce_if(edge_var)


def _add_idle_time(model: cp_model.CpModel, solver_input: SolverInput, edge: dict, arrival_time: list, activity_start: list, duration: list) -> dict:
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

    return idle_time


def _add_candidate_group_constraints(model: cp_model.CpModel, solver_input: SolverInput, is_dropped: dict) -> None:
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


def _add_excluded_stops_constraint(model: cp_model.CpModel, solver_input: SolverInput, is_dropped: dict) -> None:
    ## excluded/deleted stops
    # if a user deletes a stop, we can have the solver treat it as dead
    for stop_index in solver_input.excluded_stops:
        if stop_index in is_dropped:
            model.add(is_dropped[stop_index] == 1)


def _add_precedence_constraints(model: cp_model.CpModel, solver_input: SolverInput, arrival_time: list) -> None:
    ## precedence constraint
    # force one node to occur before the other (but not necessarily immediately before)
    for before_index, after_index in solver_input.precedences:
        gap = solver_input.nodes[before_index].duration_in_minutes
        model.add(arrival_time[after_index] - arrival_time[before_index] >= gap)


def _add_forced_edges_constraint(model: cp_model.CpModel, solver_input: SolverInput, edge: dict) -> None:
    ## forced edges constraint
    # force one node to occur immediately before the other
    for before_index, after_index in solver_input.forced_edges:
        if (before_index, after_index) in edge:
            model.add(edge[(before_index, after_index)] == 1)


def _build_objective(solver_input: SolverInput, config: SolverConfig, edge: dict, cumulative_cost: list, is_dropped: dict, idle_time: dict, duration_ext: list, num_nodes: int) -> list:
    ## objective function
    # defines how a route is scored by the time,cost and drop penalties, the solver will try to minimize this score

    time_w, cost_w, penalty_w = config.time_w, config.cost_w, config.penalty_w

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
    idle_w = config.idle_w  #penalize each minute of dead time
    for (from_index, to_index), idle_var in idle_time.items():
        objective_terms.append(idle_var * idle_w)



    ## duration extension penalty
    duration_ext_w = config.duration_ext_w
    for i in range(num_nodes):
        if i in (solver_input.start_index, solver_input.end_index):
            continue
        objective_terms.append(duration_ext[i] * duration_ext_w)

    return objective_terms


def _configure_solver(config: SolverConfig) -> cp_model.CpSolver:
    #### SOLVER
    solver = cp_model.CpSolver()

    ## MAX RUN TIME
    solver.parameters.max_time_in_seconds = config.max_time_in_seconds

    solver.parameters.num_search_workers = config.num_search_workers
    solver.parameters.enumerate_all_solutions = False

    return solver

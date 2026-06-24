from ortools.sat.python import cp_model
from models import SolverInput, SolverOutput, RouteEntry


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

    if has_virtual_end:
        terminal_index = solver_input.end_index #still virtual_end at this point
    else:
        terminal_index = route[-1].node_index


    total_route_cost = solver.Value(cumulative_cost[terminal_index])

    if has_virtual_end and route:
        virtual_entry = route.pop()  # remove dummy end node
        if round_trip:
            #add the real start node back as the final stop to represent returning home
            route.append(RouteEntry(
                node_index=solver_input.start_index,
                arrival_time_in_minutes=virtual_entry.arrival_time_in_minutes,
                departure_time_in_minutes=virtual_entry.arrival_time_in_minutes,
            ))


    # get total travel time for the route
    total_Travel_time_in_minutes = 0
    for i in range(1,len(route)):
        prev_index = route[i-1].node_index
        current_index = route[i].node_index
        total_Travel_time_in_minutes += solver_input.travel_time_matrix_in_minutes[prev_index][current_index]



    return SolverOutput (
        route=route,
        dropped_stops = dropped,
        total_time_in_minutes=total_Travel_time_in_minutes,
        total_cost_in_cents=total_route_cost,
        score= int(solver.objective_value),
        has_solution=True
    )

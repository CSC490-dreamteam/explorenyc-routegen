from dataclasses import dataclass
from models import RouteVariant

@dataclass
class SolverConfig: #tunable knobs for the solver, pulled out of generate_route so endpoints can vary them
    time_w: int #objective weight on travel time
    cost_w: int #objective weight on travel cost
    penalty_w: int #multiplier applied to each node's drop_penalty
    idle_w: int #penalty per minute of idle/dead time
    duration_ext_w: int #penalty per minute a stop's duration is stretched past its base
    max_time_in_seconds: float #solver wall-clock limit
    num_search_workers: int #parallel search workers

    @staticmethod
    def from_route_variant(route_variant: RouteVariant) -> "SolverConfig":
        #reproduces the exact hardcoded behavior generate_route used to have per variant
        if route_variant == RouteVariant.TIME_OPTIMIZED:
            time_w, cost_w = 100, 0
        elif route_variant == RouteVariant.COST_OPTIMIZED:
            time_w, cost_w = 0, 100
        else:  #BALANCED
            time_w, cost_w = 50, 50

        return SolverConfig(
            time_w=time_w,
            cost_w=cost_w,
            penalty_w=1000,
            idle_w=50,
            duration_ext_w=30,
            max_time_in_seconds=4.0,
            num_search_workers=8,
        )

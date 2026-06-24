
import uvicorn
from fastapi import FastAPI, HTTPException, status
from models import SolverInput, SolverOutput
from solver import generate_route as solve_route

app = FastAPI()

def _solve_or_422(solver_input: SolverInput) -> SolverOutput:
    output = solve_route(solver_input)
    if output.has_solution == False:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Route impossible: no solution exists given the specified parameters."
            )
    return output

@app.post("/generate_route")
def generate_route(solver_input: SolverInput) -> SolverOutput:
    return _solve_or_422(solver_input)

@app.post("/generate_advanced_route")
def generate_advanced_route(solver_input: SolverInput) -> SolverOutput:
    return _solve_or_422(solver_input)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

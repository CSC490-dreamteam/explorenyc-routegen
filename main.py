
import uvicorn
from fastapi import FastAPI, HTTPException, status
from generator import SolverInput, SolverOutput, generate_route as solve_route

app = FastAPI()

@app.post("/generate_route")
def generate_route(solver_input: SolverInput) -> SolverOutput:
    output = solve_route(solver_input)
    if output.has_solution == False:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, 
            detail="Route impossible: no solution exists given the specified parameters."
            )
    return output

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

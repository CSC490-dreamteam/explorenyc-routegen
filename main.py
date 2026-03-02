
import uvicorn
from fastapi import FastAPI
from generator import SolverInput, SolverOutput, generate_route as solve_route

app = FastAPI()

@app.post("/generate_route")
def generate_route(solver_input: SolverInput) -> SolverOutput:
    return solve_route(solver_input)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

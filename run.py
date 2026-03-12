#!/usr/bin/env python3
"""
Gremlin — Study with Gremlin Planner
Run: python3 run.py
Then open: http://localhost:5000
"""
from app import app
if __name__ == '__main__':
    print("\n  ✦ Gremlin Study Planner")
    print("  Open: http://localhost:5000\n")
    app.run(debug=True, port=5000)
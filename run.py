#!/usr/bin/env python3
"""
Gremlin — Study with Gremlin Planner
Run: python3 run.py
Then open: http://localhost:5000
"""
import os
from app import app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False,
            host='0.0.0.0',
            port=port)
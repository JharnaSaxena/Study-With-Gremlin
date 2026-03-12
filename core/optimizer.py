"""
Gremlin Schedule Optimizer v3 — Task-Based (No Fixed Time Slots)
=================================================================
Instead of assigning slots to clock times, the optimizer answers:
  "For each day, what subjects should I study, and for how long?"

Mathematical Model:
-------------------
For each subject s and day d, compute recommended_minutes(s, d):

  Step 1 — Weekly Budget (LP relaxation):
    Allocate total weekly minutes per subject proportional to priority:
    budget(s) = priority(s) / Σ priority(s') × total_weekly_minutes(type)

  Step 2 — Daily Cognitive Load Cap:
    max_daily_load(d) = base_load × wellbeing × load_scale(d)
    load_scale: Mon–Fri=1.0, Sat=0.55, Sun=0 (rest)

  Step 3 — Difficulty-Weighted Chunking:
    chunk_size(s) = base_chunk × (1 / difficulty_norm(s))
    Hard subjects (diff=5) → smaller chunks (25min Pomodoros)
    Easy subjects (diff=1) → larger chunks (50min blocks)

  Step 4 — Burnout Prevention:
    Total cognitive load per day ≤ max_daily_load
    cognitive_load(s, mins) = difficulty(s)/5 × mins/60
    If load would be exceeded → spill remainder to next available day

Priority:
  P(s) = 0.5×(difficulty/5) + 0.2×drain + 0.3×goal_weight

Spillover:
  If task not completed (user marks partial) → remaining_min carried forward
  Distributed across next N days, weighted by available capacity
"""

import math
from typing import List, Dict

DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
REST_DAY   = 'Sun'
LIGHT_DAY  = 'Sat'
LOAD_SCALE = {'Mon':1.0,'Tue':1.0,'Wed':1.0,'Thu':1.0,'Fri':1.0,'Sat':0.55,'Sun':0.0}

W_DIFF, W_DRAIN, W_GOAL = 0.50, 0.20, 0.30

SUBJECT_COLORS = [
    '#8B0000','#C9A96E','#4A7C59','#6B5B95',
    '#D4642A','#2E86AB','#A23B72','#3D5A80'
]

MODE_CFG = {
    'intense':  {'base_load': 8.0, 'base_chunk': 50, 'max_subjects_day': 5},
    'balanced': {'base_load': 5.5, 'base_chunk': 40, 'max_subjects_day': 4},
    'gentle':   {'base_load': 3.5, 'base_chunk': 25, 'max_subjects_day': 3},
}


def priority(s: Dict) -> float:
    d  = float(s.get('difficulty', 3)) / 5.0
    dr = float(s.get('drain', 0.5))
    g  = float(s.get('goal_weight', 0.5))
    return W_DIFF * d + W_DRAIN * dr + W_GOAL * g


def chunk_size(s: Dict, base: int) -> int:
    """Harder subjects → smaller focused chunks to prevent burnout."""
    diff_norm = float(s.get('difficulty', 3)) / 5.0
    # diff=5 → 0.7×base, diff=1 → 1.3×base
    factor = 1.4 - 0.7 * diff_norm
    return max(20, round(base * factor / 5) * 5)  # round to nearest 5min


def cognitive_load(s: Dict, mins: float) -> float:
    """Cognitive cost of studying subject s for `mins` minutes."""
    return (float(s.get('difficulty', 3)) / 5.0) * (mins / 60.0)


class ScheduleOptimizer:
    def __init__(self, profile: Dict):
        self.profile   = profile
        self.mode      = profile.get('mode', 'balanced')
        self.subjects  = [dict(s) for s in profile.get('subjects', [])]
        mood_raw       = profile.get('mood_override', 7)
        energy_raw     = profile.get('energy_override', 7)
        self.wellbeing = (mood_raw + energy_raw) / 20.0   # 0..1
        self.wellbeing = max(0.35, min(1.0, self.wellbeing))
        self.study_hrs = float(profile.get('available_hours', 3))
        self.goal_hrs  = float(profile.get('goal_hours', 1.5))
        self.cfg       = dict(MODE_CFG.get(self.mode, MODE_CFG['balanced']))

        # Assign colors
        for i, s in enumerate(self.subjects):
            if not s.get('color'):
                s['color'] = SUBJECT_COLORS[i % len(SUBJECT_COLORS)]

    def _weekly_budget(self) -> Dict[str, float]:
        """
        LP relaxation: distribute weekly minutes across subjects by priority.
        Returns {subject_name: weekly_minutes}
        """
        study_s = [s for s in self.subjects if s.get('type','study') == 'study']
        goal_s  = [s for s in self.subjects if s.get('type') == 'goal']

        def dist(subjs, total_mins):
            if not subjs: return {}
            tp = sum(priority(s) for s in subjs) or 1.0
            return {s['name']: priority(s) / tp * total_mins for s in subjs}

        budget = {}
        budget.update(dist(study_s, self.study_hrs * 7 * 60))
        budget.update(dist(goal_s,  self.goal_hrs  * 7 * 60))
        return budget

    def optimize(self) -> Dict:
        if not self.subjects:
            self.subjects = [
                {'name':'Core BTech',      'type':'study','difficulty':4,'drain':0.7,'goal_weight':0.8},
                {'name':'Secondary Course','type':'goal', 'difficulty':3,'drain':0.5,'goal_weight':0.9},
            ]
            for i,s in enumerate(self.subjects):
                s['color'] = SUBJECT_COLORS[i]

        weekly_budget = self._weekly_budget()
        # Remaining minutes to schedule (carries across days)
        remaining: Dict[str, float] = dict(weekly_budget)

        max_daily_load   = self.cfg['base_load'] * self.wellbeing
        max_subjects_day = self.cfg['max_subjects_day']
        base_chunk       = self.cfg['base_chunk']

        weekly: Dict[str, Dict] = {}
        total_study = total_goal = 0

        for day in DAYS:
            scale = LOAD_SCALE[day]
            if scale == 0.0:
                weekly[day] = {
                    'tasks': [], 'total_minutes': 0,
                    'goal_minutes': 0, 'daily_load': 0.0,
                    'burnout_risk': 'low', 'is_rest': True
                }
                continue

            day_load_cap = max_daily_load * scale
            day_tasks    = []
            day_load     = 0.0

            # Sort by priority desc — most important subjects studied first
            sorted_subjs = sorted(
                [s for s in self.subjects if remaining.get(s['name'], 0) > 5],
                key=priority, reverse=True
            )[:max_subjects_day]

            for subj in sorted_subjs:
                if day_load >= day_load_cap:
                    break

                avail_rem = remaining.get(subj['name'], 0)
                if avail_rem <= 0:
                    continue

                # How much load capacity is left for this subject today?
                load_left = day_load_cap - day_load
                # Max minutes this subject can take given remaining load
                max_mins_by_load = (load_left / (subj.get('difficulty',3)/5.0)) * 60
                max_mins_by_load = max(0, max_mins_by_load)

                # Allocate: spread evenly across active days
                active_days_left = sum(
                    1 for d in DAYS[DAYS.index(day):]
                    if LOAD_SCALE[d] > 0
                )
                daily_share = avail_rem / max(1, active_days_left)

                # Round to nearest chunk size
                cs      = chunk_size(subj, base_chunk)
                planned = min(daily_share, max_mins_by_load, avail_rem)
                planned = max(cs, round(planned / cs) * cs)  # round to chunks
                planned = min(planned, avail_rem, max_mins_by_load)
                planned = round(planned / 5) * 5             # round to 5min

                if planned < 15:
                    continue

                load_cost = cognitive_load(subj, planned)
                day_load  += load_cost
                remaining[subj['name']] -= planned

                # Build pomodoro sessions
                sessions = _build_sessions(planned, cs)

                day_tasks.append({
                    'subject':      subj['name'],
                    'type':         subj.get('type', 'study'),
                    'color':        subj.get('color', '#8B0000'),
                    'difficulty':   subj.get('difficulty', 3),
                    'priority':     round(priority(subj), 2),
                    'total_min':    planned,
                    'sessions':     sessions,       # list of {min, label}
                    'chunk_min':    cs,
                    'done_min':     0,              # filled by frontend
                    'completed':    False,
                })
                if subj.get('type') == 'goal':
                    total_goal += planned
                else:
                    total_study += planned

            total_min = sum(t['total_min'] for t in day_tasks)
            risk_ratio = day_load / max(day_load_cap, 0.01)
            risk = 'low' if risk_ratio < 0.55 else ('medium' if risk_ratio < 0.82 else 'high')

            weekly[day] = {
                'tasks':        day_tasks,
                'total_minutes': total_min,
                'goal_minutes': sum(t['total_min'] for t in day_tasks if t['type']=='goal'),
                'daily_load':   round(day_load, 2),
                'burnout_risk': risk,
                'is_rest':      False
            }

        # Stats
        all_risks  = [weekly[d]['burnout_risk'] for d in DAYS if not weekly[d]['is_rest']]
        risk_map   = {'low':0,'medium':1,'high':2}
        avg_risk   = sum(risk_map[r] for r in all_risks) / max(len(all_risks),1)
        overall    = 'low' if avg_risk < 0.55 else ('medium' if avg_risk < 1.4 else 'high')
        target_min = (self.study_hrs + self.goal_hrs) * 7 * 60
        efficiency = min(100, round((total_study + total_goal) / max(target_min,1) * 100))
        penalty    = {'low':0,'medium':12,'high':38}[overall]
        opt_score  = max(0, round(efficiency - penalty))

        return {
            'weekly': weekly,
            'stats': {
                'total_study_min':    total_study,
                'total_goal_min':     total_goal,
                'burnout_risk':       overall,
                'optimization_score': opt_score,
                'efficiency':         efficiency,
                'mode':               self.mode,
                'wellbeing':          round(self.wellbeing, 2),
                'subjects': [
                    {'name': s['name'], 'color': s.get('color','#8B0000'),
                     'priority': round(priority(s), 2)}
                    for s in self.subjects
                ],
                'adherence_target': 60,
                'rest_day':  REST_DAY,
                'light_day': LIGHT_DAY,
            }
        }


def _build_sessions(total_min: int, chunk_min: int) -> List[Dict]:
    """Split total_min into Pomodoro-style sessions."""
    sessions = []
    left = total_min
    n    = 1
    while left > 0:
        mins = min(chunk_min, left)
        sessions.append({'min': mins, 'label': f'Session {n}', 'done': False})
        left -= mins
        n    += 1
    return sessions
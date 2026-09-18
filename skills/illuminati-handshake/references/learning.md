# When a learning experiment is justified

Use RL only after defining an observable reward and a resettable environment that
can support repeated bounded trials. State why a deterministic baseline, existing
library, search, or supervised method is insufficient. If reward cannot measure
the needed outcome reliably, improve the evaluation or choose a simpler method.

Create the experiment with `capability_lab_create(..., method="rl")`. Alongside
the shared lab files it writes `environment.py` and `train.py`: a runnable CPU
tabular Q-learning calibration environment. After inspecting those files, a
bounded calibration run uses `python3 train.py --episodes 200` from the returned
lab directory through ordinary `run_bash` (quote the directory when using `cd`).
This checks the scaffold only. It trains no language model, produces no model
weights, and is not a solution to the user's domain task.

Replace `environment.py` with the specific task environment and adapt the
candidate/evaluation code before a domain experiment. Define independent held-out
acceptance checks beyond its training reward. A reported successful calibration
or a hash returned by `capability_lab_inspect(id)` cannot substitute for those
checks. No lab call automatically runs training or installs the resulting code.

Separate environment implementation, training cases, validation cases, and held-out
evaluation. Check reset/termination behavior, action limits, seed handling, reward
range, and failure penalties with synthetic CPU trials before any model work.
Test whether an agent can exploit the reward while failing the real task; include
those exploits in evaluation. Keep evaluation fixtures out of training updates.

Choose the smallest adequate learning setup. Record trial and wall-clock limits,
checkpoint frequency, compute/storage estimates, and explicit stop conditions.
Inspect device availability, free memory, competing work, and required credentials
through resource preflight. An available GPU does not authorize a model load or
training run. Without the necessary authorization/resources, deliver the runnable
scaffold and evaluation plan as an unexecuted experiment, clearly labeled.

Report training reward separately from held-out success, baseline comparison,
variance across seeds, failure outcomes, and compute cost. Do not promote a policy
because reward rose. Preserve checkpoints and rollback evidence; activation and
replacement follow the same review and enable controls as other capabilities.

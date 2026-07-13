import json, os

files = [
    'log_260616_1047.conversation.json',
    'log_260618_0009.conversation.json',
    'log_260618_0025.conversation.json',
    'log_260618_0049.conversation.json',
    'log_260618_0102.conversation.json',
]

for f in files:
    path = f'c:/project/aut_agent/{f}'
    size = os.path.getsize(path)
    with open(path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    
    history = data.get('history', [])
    num_steps = len(history)
    
    # Find final result
    final_result = None
    is_done = False
    last_action_type = None
    for item in reversed(history):
        mo = item.get('model_output')
        if not mo:
            continue
        if isinstance(mo, dict):
            action = mo.get('action', [])
        else:
            action = getattr(mo, 'action', []) or []
        if isinstance(action, list) and len(action) > 0:
            last_action_type = list(action[0].keys()) if isinstance(action[0], dict) else 'unknown'
            if isinstance(action[0], dict) and action[0].get('done'):
                final_result = action[0]['done'].get('text', '')[:500]
                is_done = True
            break
    
    # Count errors
    errors = 0
    for item in history:
        state = item.get('state')
        if state and isinstance(state, dict) and state.get('error'):
            errors += 1
    
    # Get last extracted content
    last_extracted = ''
    for item in reversed(history):
        state = item.get('state')
        if state and isinstance(state, dict):
            ec = state.get('extracted_content')
            if ec:
                last_extracted = str(ec)[:300]
                break
    
    # Check for key script actions
    script_actions = []
    for item in history:
        mo = item.get('model_output')
        if not mo:
            continue
        if isinstance(mo, dict):
            action = mo.get('action', [])
        else:
            action = getattr(mo, 'action', []) or []
        if isinstance(action, list):
            for a in action:
                if isinstance(a, dict):
                    for k in a.keys():
                        if k in ('script_login', 'script_prefill_form', 'script_submit_form', 'script_close_popups'):
                            script_actions.append(k)
    
    print(f'=== {f} ({size/1024:.0f}KB, {num_steps} steps) ===')
    print(f'  is_done: {is_done}')
    print(f'  errors: {errors}')
    print(f'  last_action: {last_action_type}')
    print(f'  script_actions: {script_actions}')
    if final_result:
        print(f'  final_result: {final_result[:300]}')
    if last_extracted:
        print(f'  last_extracted: {last_extracted[:200]}')
    print()
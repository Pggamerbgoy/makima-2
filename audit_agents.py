import ast, os, sys
sys.stdout.reconfigure(encoding='utf-8')
agents_dir = 'apps/brain/agents'
issues = []
for f in sorted(os.listdir(agents_dir)):
    if f.endswith('.py') and f != '__init__.py':
        path = os.path.join(agents_dir, f)
        try:
            src = open(path, encoding='utf-8', errors='ignore').read()
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and 'Agent' in node.name:
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                            args = [a.arg for a in item.args.args]
                            ok = 'ai_handler' in args
                            status = 'OK ' if ok else 'FIX'
                            print(f'[{status}] {f}: {node.name}.__init__({args})')
                            if not ok:
                                issues.append(f)
        except SyntaxError as e:
            print(f'[SYNTAX] {f}: {e}')
            issues.append(f)
        except Exception as e:
            print(f'[ERR  ] {f}: {e}')

print()
if issues:
    print(f'NEEDS FIX: {issues}')
else:
    print('ALL AGENTS OK!')

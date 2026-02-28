#!/usr/bin/env python3
import os, re

os.chdir(os.path.dirname(os.path.abspath(__file__)))

files = [
    'miniprogram/utils/ai.js',
    'miniprogram/utils/market.js',
    'miniprogram/utils/storage.js',
    'miniprogram/utils/advisor.js',
    'miniprogram/utils/analyzer.js',
    'miniprogram/utils/api.js',
    'miniprogram/pages/dashboard/index.js',
    'miniprogram/pages/settings/index.js',
    'miniprogram/pages/holdings/index.js',
    'miniprogram/pages/sentiment/index.js',
]

print("=== JS Bracket Check ===")
for f in files:
    try:
        content = open(f).read()
        stack = []
        err = None
        for i, ch in enumerate(content):
            if ch in '({[':
                stack.append((ch, i))
            elif ch in ')}]':
                if not stack:
                    line = content[:i].count('\n') + 1
                    err = 'unmatched closing "%s" at line %d' % (ch, line)
                    break
                o = stack.pop()[0]
                expect = {')':'(', '}':'{', ']':'['}
                if o != expect[ch]:
                    line = content[:i].count('\n') + 1
                    err = 'mismatch "%s"..."%s" at line %d' % (o, ch, line)
                    break
        if err:
            print('FAIL %s: %s' % (f, err))
        elif stack:
            ch, pos = stack[-1]
            line = content[:pos].count('\n') + 1
            print('FAIL %s: unclosed "%s" at line %d' % (f, ch, line))
        else:
            print('OK   %s (%d lines)' % (f, len(content.splitlines())))
    except Exception as e:
        print('ERR  %s: %s' % (f, e))

print("\n=== WXML Tag Balance ===")
wxml_files = [
    'miniprogram/pages/dashboard/index.wxml',
    'miniprogram/pages/sentiment/index.wxml',
    'miniprogram/pages/holdings/index.wxml',
    'miniprogram/pages/settings/index.wxml',
]
for f in wxml_files:
    content = open(f).read()
    tags = re.findall(r'<(/?)(\w+)[\s>]', content)
    counts = {}
    for close, name in tags:
        if name in ('image', 'input', 'import', 'include', 'wxs'):
            continue
        if name not in counts:
            counts[name] = {'open': 0, 'close': 0}
        if close == '/':
            counts[name]['close'] += 1
        else:
            counts[name]['open'] += 1
    bad = False
    for tag, c in sorted(counts.items()):
        if c['open'] != c['close']:
            print('FAIL %s: <%s> open=%d close=%d' % (f, tag, c['open'], c['close']))
            bad = True
    if not bad:
        print('OK   %s (all tags balanced)' % f)

print("\n=== Module Exports Check ===")
# Check that ai.js exports everything dashboard needs
ai_content = open('miniprogram/utils/ai.js').read()
m = re.search(r'module\.exports\s*=\s*\{([^}]+)\}', ai_content)
if m:
    exports = [x.strip().rstrip(',') for x in m.group(1).split('\n') if x.strip() and not x.strip().startswith('//')]
    print('ai.js exports: %s' % ', '.join(exports))
else:
    print('ai.js: NO module.exports found!')

# Check dashboard imports from ai
dash_content = open('miniprogram/pages/dashboard/index.js').read()
m2 = re.search(r"const\s*\{([^}]+)\}\s*=\s*require\('../../utils/ai'\)", dash_content)
if m2:
    imports = [x.strip() for x in m2.group(1).split(',')]
    print('dashboard imports from ai: %s' % ', '.join(imports))
    for imp in imports:
        if imp not in ai_content:
            print('  WARNING: "%s" might not be exported from ai.js!' % imp)


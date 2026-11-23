#!/usr/bin/env python3
"""
split_question_data.py  -  one-off converter for the question data files.

WHY
  Every questions_*.js file is ~99% base64 image text, and the page used to download all of them (~170 MB)
  on every load.  This script cuts them into small bundles so the page only fetches what is being viewed:

      qdata/manifest.js               small index the page reads at startup
      qdata/p1/<objective>.js         Practise Paper 1 questions of ONE objective (e.g. qdata/p1/2.1.js, qdata/p1/DH.js)
      qdata/p2/<objective>.js         Practise Paper 2 questions of ONE objective
      qdata/ctx/<paper>.js            case-study images of ONE Paper 2 paper (fetched only when its button is opened)

WHAT IT DOES NOT CHANGE
  Question text, geometry, images and their order are copied byte-for-byte.  The page's reclassification table (RC)
  and removal table (RM) are applied here, exactly as the page applied them at load time.  The script then re-reads
  everything it wrote and compares it with the source, and stops with an error if anything differs.

USAGE
  Put this script in the folder that holds the page (index_merged.html) and the questions_*.js files, then run:

      python3 split_question_data.py            # or:  python3 split_question_data.py /path/to/that/folder
      python3 split_question_data.py . index.html     # only if the page file has another name

  Options:
      --max-mb N     largest size of one file in MB (default 10).  An objective whose data is bigger is written as
                     several part files that the page loads together.  GitHub's website refuses files over 25 MB.
      --batches      also make upload_batches/batch01, batch02 ... each small enough to upload to GitHub's website
                     in one go (drag the qdata folder from ONE batch at a time, then commit; repeat for the next batch).

  Then commit the new qdata/ folder next to index_merged.html.  Keep the old questions_*.js files until you have
  checked the site; once it works they can be deleted (the page only falls back to them if qdata/ is missing).
  Run the script again whenever you add a paper (just put its questions_*.js file in the folder) or change RC/RM in the page.
"""
import argparse, hashlib, json, os, re, shutil, sys

HTML_NAME = 'index_merged.html'
OUT_DIR = 'qdata'

RE_IMG = re.compile(r'^Q\.img\["([^"]+)"\]=\{d:"(data:image/[a-z]+;base64,[A-Za-z0-9+/=]*)",w:(\d+),h:(\d+)\};$')
RE_PH = re.compile(r'^Q\.ph\["([^"]+)"\]=(\[.*\]);$')
RE_UNIT = re.compile(r'^Q\.units\.push\((\{.*\})\);$')
RE_PAPERS = re.compile(r'^Q\.papers\["([^"]+)"\]=1;$')


def die(msg):
    print('\nERROR: ' + msg, file=sys.stderr)
    sys.exit(1)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def sha(text, n=8):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:n]


def is_p2(paper):
    return re.match(r'^0680/2', str(paper)) is not None


# ---------------------------------------------------------------------------------------------------------------
# 1. what the page expects: file order and the RC / RM tables
# ---------------------------------------------------------------------------------------------------------------
def find_page(folder, wanted=None):
    """The page is the .html file that contains the question file list (marker EMQ_LEGACY)."""
    if wanted:
        path = os.path.join(folder, wanted)
        if not os.path.exists(path):
            die(f'{wanted} not found in {folder}.')
        return path
    names = [HTML_NAME, 'index.html'] + sorted(n for n in os.listdir(folder) if n.lower().endswith('.html'))
    for n in names:
        path = os.path.join(folder, n)
        if os.path.isfile(path) and '/*EMQ_LEGACY_START*/' in read(path):
            return path
    die(f'no page with the question file list was found in {folder}.\n'
        f'  Put this script in the same folder as the NEW {HTML_NAME} (the version that goes with it) and run it there.')


def read_page(folder, wanted=None):
    path = find_page(folder, wanted)
    html = read(path)
    m = re.search(r'/\*EMQ_LEGACY_START\*/window\.EMQ_LEGACY=(\{.*?\});/\*EMQ_LEGACY_END\*/', html, re.S)
    legacy = json.loads(m.group(1))
    m = re.search(r"var RC=\{(.*?)\};var RM=\{(.*?)\};", html, re.S)
    if not m:
        die('could not find the RC / RM tables in the page.')
    rc = dict(re.findall(r"'([^']+)':'([^']+)'", m.group(1)))
    rm = set(re.findall(r"'([^']+)':\d+", m.group(2)))
    return legacy, rc, rm, os.path.basename(path)


# ---------------------------------------------------------------------------------------------------------------
# 2. strict parser for one data file (every line must be a known kind, otherwise stop)
# ---------------------------------------------------------------------------------------------------------------
def parse_data_file(path):
    items = []                      # ('img', id, line) | ('ph', id, line) | ('unit', obj, line) | ('papers', name, line)
    with open(path, encoding='utf-8') as f:
        lines = f.read().replace('\r\n', '\n').split('\n')
    in_comment = False
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if in_comment:
            if '*/' in s:
                in_comment = False
            continue
        if s == '':
            continue
        if s.startswith('/*'):
            if '*/' not in s:
                in_comment = True
            continue
        if s.startswith('(function(){var Q=window.QB=') or s == '})();':
            continue
        m = RE_IMG.match(line)
        if m:
            items.append(('img', m.group(1), line)); continue
        m = RE_PH.match(line)
        if m:
            json.loads(m.group(2)); items.append(('ph', m.group(1), line)); continue
        m = RE_UNIT.match(line)
        if m:
            items.append(('unit', json.loads(m.group(1)), line)); continue
        m = RE_PAPERS.match(line)
        if m:
            items.append(('papers', m.group(1), line)); continue
        die(f'{os.path.basename(path)} line {n}: unexpected content, not converting.\n  ' + line[:160])
    return items


def collect_strings(obj, out):
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_strings(v, out)


# ---------------------------------------------------------------------------------------------------------------
# 3. main
# ---------------------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('folder', nargs='?', default=os.getcwd())
    ap.add_argument('html', nargs='?', default=None)
    ap.add_argument('--max-mb', type=float, default=10.0)
    ap.add_argument('--batches', action='store_true')
    opt = ap.parse_args()
    folder = os.path.abspath(opt.folder)
    max_bytes = int(opt.max_mb * 1048576)
    legacy, RC, RM, page_name = read_page(folder, opt.html)
    print(f'Folder: {folder}\nPage: {page_name}  (lists {len(legacy["p1"])} Paper 1 files and {len(legacy["p2"])} Paper 2 files; '
          f'RC has {len(RC)} entries, RM has {len(RM)})')
    # papers added after the page was built: any other questions_*.js in the folder is picked up and put after the listed ones
    known = set(legacy['p1']) | set(legacy['p2']) | {legacy['ctx']}
    extra1 = sorted(n for n in os.listdir(folder) if re.match(r'^questions_0680-\d+_.+\.js$', n) and n not in known)
    extra2 = sorted(n for n in os.listdir(folder) if re.match(r'^questions_P2_0680-\d+_.+\.js$', n) and n not in known)
    if extra1 or extra2:
        print('New files found that the page does not list (they are included): ' + ', '.join(extra1 + extra2))
        legacy = dict(legacy, p1=legacy['p1'] + extra1, p2=legacy['p2'] + extra2)

    # ---- read the data in the same order the page loaded it (this order decides the order of the questions)
    papers = {1: {'img': {}, 'imgorder': {}, 'ph': {}, 'units': []}, 2: {'img': {}, 'imgorder': {}, 'ph': {}, 'units': []}}
    legacy_bytes = 0
    n_files = 0
    missing = []
    for pt, names in ((1, legacy['p1']), (2, legacy['p2'])):
        P = papers[pt]
        for name in names:
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                missing.append(name)
                continue
            n_files += 1
            legacy_bytes += os.path.getsize(path)
            for kind, key, line in parse_data_file(path):
                if kind == 'img':
                    P['img'][key] = line                       # later lines win, like the page
                    P['imgorder'].setdefault(key, len(P['imgorder']))
                elif kind == 'ph':
                    P['ph'][key] = line
                elif kind == 'unit':
                    P['units'].append((key, line))
    print(f'Read {n_files} data files ({legacy_bytes / 1048576:.1f} MB).')
    if missing:
        print(f'{len(missing)} listed files were not found and were skipped (the old page ignored missing files too), for example: ' + ', '.join(missing[:3]) + (' ...' if len(missing) > 3 else ''))

    # ---- apply RM / RC like the page, then bucket by objective keeping the original order
    out_root = os.path.join(folder, OUT_DIR)
    if os.path.isdir(out_root):
        shutil.rmtree(out_root)
    os.makedirs(os.path.join(out_root, 'p1')); os.makedirs(os.path.join(out_root, 'p2')); os.makedirs(os.path.join(out_root, 'ctx'))

    manifest = {'p1': {}, 'p2': {}, 'ctx': {}}
    expected = {}                      # (pt, sec) -> list of unit dicts, for the final self-check
    expected_img = {}                  # (pt, sec) -> {id: line}
    stats = []
    for pt in (1, 2):
        P = papers[pt]
        buckets = {}
        removed = 0
        for u, line in P['units']:
            if is_p2(u.get('paper')) != (pt == 2):
                die(f'a unit of paper "{u.get("paper")}" was found in a Paper {pt} file.')
            k = f"{u['paper']}|{u['q']}{u['part']}"
            if k in RM:
                removed += 1
                continue
            if k in RC and RC[k] != u['sec']:
                u = dict(u); u['sec'] = RC[k]
                line = 'Q.units.push(' + json.dumps(u) + ');'
            buckets.setdefault(u['sec'], []).append((u, line))
        print(f'Paper {pt}: {len(P["units"])} questions read, {removed} removed by RM, {sum(len(v) for v in buckets.values())} kept in {len(buckets)} objectives.')

        for sec in sorted(buckets):
            units = buckets[sec]

            def closure(ids, sec=sec):
                """every image a set of ids needs: the images themselves, their overlays (ph) and the images the overlays point to"""
                need, todo, phl = set(), [i for i in ids if i in P['img']], {}
                while todo:
                    i = todo.pop()
                    if i in need:
                        continue
                    need.add(i)
                    if i in P['ph']:
                        phl[i] = P['ph'][i]
                        for o in json.loads(RE_PH.match(P['ph'][i]).group(2)):
                            if o.get('i') in P['img']:
                                todo.append(o['i'])
                            else:
                                print(f'  ! overlay image {o.get("i")} of {i} is missing in the data (objective {sec})')
                return need, phl

            for u, _ in units:
                for fld in ('qp', 'ms', 'stem'):
                    if u.get(fld) and u[fld] not in P['img']:
                        print(f'  ! {fld} image {u[fld]} of {u["paper"]} Q{u["q"]}{u["part"]} is missing in the data')

            # cut the objective into parts no bigger than --max-mb (a question is never split; its images travel with it)
            chunks, cur, cur_need, cur_size = [], [], set(), 0
            for u, line in units:
                strs = set(); collect_strings(u, strs)
                need_u, _ = closure(strs)
                add = sum(len(P['img'][i]) for i in need_u - cur_need) + len(line) + 200
                if cur and cur_size + add > max_bytes:
                    chunks.append(cur); cur, cur_need, cur_size = [], set(), 0
                    add = sum(len(P['img'][i]) for i in need_u) + len(line) + 200
                cur.append((u, line)); cur_need |= need_u; cur_size += add
            if cur:
                chunks.append(cur)

            files = []
            for ci, chunk in enumerate(chunks, 1):
                strings = set()
                for u, _ in chunk:
                    collect_strings(u, strings)
                need, ph_lines = closure(strings)
                names = sorted({u['paper'] for u, _ in chunk})
                part = '' if len(chunks) == 1 else f' (part {ci} of {len(chunks)})'
                body = ['/* Practise Paper %d, objective %s%s: generated by split_question_data.py. Do not edit by hand. */' % (pt, sec, part),
                        '(function(){var Q=window.QB=window.QB||{img:{},ph:{},units:[],papers:{}};']
                body += ['Q.papers[%s]=1;' % json.dumps(n) for n in names]
                body += [P['img'][i] for i in sorted(need, key=lambda x: P['imgorder'][x])]
                body += [ph_lines[i] for i in sorted(ph_lines, key=lambda x: P['imgorder'][x])]
                body += [line for _, line in chunk]
                body.append('})();')
                text = '\n'.join(body) + '\n'
                fname = f'{OUT_DIR}/p{pt}/{sec}.js' if len(chunks) == 1 else f'{OUT_DIR}/p{pt}/{sec}.part{ci}.js'
                with open(os.path.join(folder, fname), 'w', encoding='utf-8', newline='\n') as f:
                    f.write(text)
                files.append(f'{fname}?v={sha(text)}')
                stats.append((pt, sec, len(chunk), len(text), fname))
            manifest['p%d' % pt][sec] = {'f': files[0] if len(files) == 1 else files, 'n': len(units)}
            expected[(pt, sec)] = [u for u, _ in units]

    # ---- case-study images: one small file per Paper 2 paper
    ctx_path = os.path.join(folder, legacy['ctx'])
    ctx_expected = {}
    if os.path.exists(ctx_path):
        txt = read(ctx_path).strip()
        m = re.match(r'^window\.QCTX=\{(.*)\};?$', txt, re.S)
        if not m:
            die(f'{legacy["ctx"]} has an unexpected shape.')
        entries = re.findall(r'"([^"]+)":\s*(\[[^\]]*\])', m.group(1))
        if not entries:
            die(f'no case-study entries found in {legacy["ctx"]}.')
        for paper, arr in entries:
            json.loads(arr)
            slug = re.sub(r'[^A-Za-z0-9._-]+', '_', paper.replace('/', '-')).strip('_')
            text = '(window.QCTX=window.QCTX||{})[%s]=%s;\n' % (json.dumps(paper), arr)
            fname = f'{OUT_DIR}/ctx/{slug}.js'
            with open(os.path.join(folder, fname), 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
            manifest['ctx'][paper] = f'{fname}?v={sha(text)}'
            ctx_expected[paper] = arr
        print(f'Case-study images: {len(entries)} papers, one file each.')
    else:
        print(f'  - {legacy["ctx"]}: not found, no case-study files written')

    version = sha(json.dumps(manifest, sort_keys=True), 10)
    with open(os.path.join(out_root, 'manifest.js'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('/* generated by split_question_data.py */\nwindow.QDATA=' + json.dumps({'v': version, **manifest}, indent=0) + ';\n')

    # -----------------------------------------------------------------------------------------------------------
    # 4. self-check: read every file that was written and compare it with what the old files said
    # -----------------------------------------------------------------------------------------------------------
    problems = 0
    total_units = 0
    for (pt, sec), want_units in expected.items():
        entry = manifest['p%d' % pt][sec]
        flist = entry['f'] if isinstance(entry['f'], list) else [entry['f']]
        g_units = []
        for ref in flist:
            got = parse_data_file(os.path.join(folder, ref.split('?')[0]))
            part_units = [k for kind, k, _ in got if kind == 'unit']
            g_units += part_units
            have = {}
            for kind, k, l in got:
                if kind == 'img':
                    have[k] = l
            for i, l in have.items():
                if papers[pt]['img'].get(i) != l:
                    print(f'  CHECK FAILED: an image of Paper {pt} objective {sec} differs from the source'); problems += 1
            for u in part_units:
                strings = set(); collect_strings(u, strings)
                for i in strings:
                    if i in papers[pt]['img'] and i not in have:
                        print(f'  CHECK FAILED: image {i} is missing from {ref.split("?")[0]}'); problems += 1
        if g_units != want_units:
            print(f'  CHECK FAILED: units of Paper {pt} objective {sec} differ'); problems += 1
        total_units += len(g_units)
    src_units = sum(len(v) for v in expected.values())
    if total_units != src_units:
        print('  CHECK FAILED: question count differs'); problems += 1
    for paper, arr in ctx_expected.items():
        t = read(os.path.join(folder, manifest['ctx'][paper].split('?')[0]))
        if arr not in t:
            print(f'  CHECK FAILED: case-study file of {paper} differs'); problems += 1
    if problems:
        die(f'{problems} check(s) failed. The qdata folder was written but must NOT be used.')

    # ---- report
    all_files = sorted((os.path.join(dp, fn) for dp, _, fns in os.walk(out_root) for fn in fns))
    new_bytes = sum(os.path.getsize(f) for f in all_files)
    biggest = sorted(((os.path.getsize(f), os.path.relpath(f, folder).replace(os.sep, '/')) for f in all_files), reverse=True)[:5]
    objective_sizes = {}
    for pt, sec, n, size, fname in stats:
        objective_sizes[(pt, sec)] = objective_sizes.get((pt, sec), 0) + size
    osz = sorted(objective_sizes.values())
    parts = sum(1 for st in stats if '.part' in st[4])
    print('\nChecked: every question, image and case-study file in qdata/ matches the old files.')
    print(f'  files written: {len(all_files)} (largest allowed: {opt.max_mb:g} MB; {parts} part files because an objective was bigger than that)')
    print(f'  data per objective (what one Practise Paper tab downloads): median {osz[len(osz) // 2] / 1048576:.2f} MB, '
          f'largest {osz[-1] / 1048576:.2f} MB, smallest {osz[0] / 1048576:.2f} MB')
    print('  biggest single files: ' + ', '.join(f'{n} {b / 1048576:.1f} MB' for b, n in biggest))
    if biggest[0][0] > 25 * 1048576:
        print('  ! a single file is over 25 MB: GitHub\'s website will refuse it. Upload with GitHub Desktop, or run again with a smaller --max-mb.')
    print(f'  data the old page loaded at startup: {legacy_bytes / 1048576:.1f} MB   ->   now at startup: '
          f'{os.path.getsize(os.path.join(out_root, "manifest.js")) / 1024:.0f} KB (manifest only)')
    print(f'  total size of qdata/: {new_bytes / 1048576:.1f} MB (the old questions_*.js files can be deleted once the site is checked)')

    if opt.batches:
        bdir = os.path.join(folder, 'upload_batches')
        if os.path.isdir(bdir):
            shutil.rmtree(bdir)
        batches, cur, cur_size = [], [], 0
        for f in all_files:                                   # manifest.js sorts first, so it is in batch01
            size = os.path.getsize(f)
            if cur and (cur_size + size > 40 * 1048576 or len(cur) >= 60):
                batches.append(cur); cur, cur_size = [], 0
            cur.append(f); cur_size += size
        if cur:
            batches.append(cur)
        for bi, files in enumerate(batches, 1):
            for f in files:
                dest = os.path.join(bdir, f'batch{bi:02d}', os.path.relpath(f, folder))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(f, dest)
        print(f'\nUpload batches: {len(batches)} folders in upload_batches/ (each under 40 MB and 60 files).')
        print('  For each batch, in order: open batch01/ and drag its qdata folder onto GitHub\'s "Upload files" page, commit, then batch02, and so on.')
        print('  (Do not upload the upload_batches folder itself.  Delete it when you are done.)')

    print(f'\nDone. Upload the new {page_name} and the whole qdata/ folder to your site.')


if __name__ == '__main__':
    main()

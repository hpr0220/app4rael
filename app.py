"""부모님 가게의 다음 주 장사 도우미
실행: streamlit run app.py
CSV 한 파일에 메뉴(MENU), 날짜(DAY), 판매(SALE)를 구분해 보관합니다.
이 앱은 서버 파일에 기록하지 않습니다. 매일 CSV를 내려받아 보관하세요.
"""
import math
import io
import uuid
from decimal import Decimal
from datetime import date, timedelta
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
import altair as alt
import qrcode

WEEK = ['월', '화', '수', '목', '금', '토', '일']
COLS = ['유형', '메뉴ID', '메뉴명', '기본단가', '기본재료비', '사용중', '영업일',
        '영업상태', '기록완료', '판매수량', '판매단가', '개당재료비', '폐기수량',
        '품절여부', '놓친주문', '특이사항']


def empty_store():
    return {'menus': [], 'days': {}, 'sales': []}


def missing(x):
    return x is None or pd.isna(x) or (isinstance(x, str) and not x.strip())


def number(x, label, integer=False, optional=False):
    # 빈칸은 0으로 바꾸지 않습니다. NaN/무한대/음수/소수 수량도 거절합니다.
    if missing(x):
        if optional:
            return None
        raise ValueError(f'{label}: 값을 입력하세요.')
    try:
        n = float(x)
    except (ValueError, TypeError):
        raise ValueError(f'{label}: 숫자가 아닙니다.')
    if not math.isfinite(n) or n < 0 or (integer and n != int(n)):
        raise ValueError(f'{label}: 0 이상의 {"정수" if integer else "유한한 숫자"}만 가능합니다.')
    return int(n) if integer else n


def boolean(x, label):
    if str(x).strip().lower() in ['true', '1']:
        return True
    if str(x).strip().lower() in ['false', '0']:
        return False
    raise ValueError(f'{label}: TRUE 또는 FALSE만 가능합니다.')


def parse_date(x):
    try:
        s = str(x).strip()
        d = date.fromisoformat(s)
        if d.isoformat() != s:
            raise ValueError()
        return d
    except ValueError:
        raise ValueError(f'날짜 {x}: YYYY-MM-DD 형식을 사용하세요.')


def export_csv(store):
    rows = []
    for m in store['menus']:
        rows.append({'유형': 'MENU', '메뉴ID': m['id'], '메뉴명': m['name'],
                     '기본단가': m['price'], '기본재료비': m['cost'], '사용중': m['active']})
    for ds, d in sorted(store['days'].items()):
        rows.append({'유형': 'DAY', '영업일': ds, '영업상태': d['status'], '기록완료': d['complete']})
    for r in store['sales']:
        rows.append({'유형': 'SALE', '메뉴ID': r['id'], '영업일': r['date'],
                     '판매수량': r['qty'], '판매단가': r['price'], '개당재료비': r['cost'],
                     '폐기수량': r['waste'], '품절여부': r['out'],
                     '놓친주문': r['lost'], '특이사항': r['note']})
    return pd.DataFrame(rows, columns=COLS).to_csv(index=False).encode('utf-8-sig')


def import_csv(raw):
    df = pd.read_csv(io.BytesIO(raw), encoding='utf-8-sig', dtype=str, keep_default_na=False)
    if len(df.columns) != len(COLS) or set(df.columns) != set(COLS):
        raise ValueError('열 이름이 올바르지 않습니다. 이 앱에서 내려받은 CSV 형식을 사용하세요. 필요 열: ' + ', '.join(COLS))
    if not df['유형'].isin(['MENU', 'DAY', 'SALE']).all():
        raise ValueError('유형은 MENU, DAY, SALE만 가능합니다.')
    s = empty_store()
    ids, names = set(), set()
    for _, r in df[df['유형'] == 'MENU'].iterrows():
        mid, name = r['메뉴ID'].strip(), r['메뉴명'].strip()
        if not mid or not name or mid in ids or name in names:
            raise ValueError('메뉴ID/메뉴명 빈칸 또는 중복이 있습니다.')
        ids.add(mid)
        names.add(name)
        s['menus'].append({'id': mid, 'name': name, 'price': number(r['기본단가'], '기본단가'),
                           'cost': number(r['기본재료비'], '기본재료비', optional=True),
                           'active': boolean(r['사용중'], '사용중')})
    for _, r in df[df['유형'] == 'DAY'].iterrows():
        ds = parse_date(r['영업일']).isoformat()
        if ds in s['days'] or r['영업상태'] not in ['영업', '휴무']:
            raise ValueError('중복 날짜 또는 잘못된 영업상태가 있습니다.')
        complete = boolean(r['기록완료'], '기록완료')
        if r['영업상태'] == '휴무' and complete:
            raise ValueError('휴무일은 기록완료=FALSE로 저장하세요.')
        s['days'][ds] = {'status': r['영업상태'], 'complete': complete}
    keys = set()
    for _, r in df[df['유형'] == 'SALE'].iterrows():
        ds, mid = parse_date(r['영업일']).isoformat(), r['메뉴ID'].strip()
        key = (ds, mid)
        if mid not in ids or ds not in s['days'] or key in keys:
            raise ValueError('판매 기록의 메뉴/날짜 참조 오류 또는 같은 날짜·메뉴 중복입니다.')
        if s['days'][ds]['status'] == '휴무':
            raise ValueError('휴무일에 판매 기록이 있습니다.')
        if parse_date(ds) > date.today():
            raise ValueError('미래 날짜에는 판매 기록을 입력할 수 없습니다. 예정 휴무는 DAY 행으로 관리하세요.')
        keys.add(key)
        out = boolean(r['품절여부'], '품절여부')
        lost = number(r['놓친주문'], '놓친주문', integer=True, optional=True)
        if not out and lost not in [None, 0]:
            raise ValueError('놓친 주문이 있으면 품절여부를 TRUE로 설정하세요.')
        s['sales'].append({'id': mid, 'date': ds, 'qty': number(r['판매수량'], '판매수량', True),
                           'price': number(r['판매단가'], '판매단가'),
                           'cost': number(r['개당재료비'], '개당재료비', optional=True),
                           'waste': number(r['폐기수량'], '폐기수량', True),
                           'out': out, 'lost': lost, 'note': r['특이사항']})
    for ds, d in s['days'].items():
        if d['complete'] and (parse_date(ds) > date.today() or not any(r['date'] == ds for r in s['sales'])):
            raise ValueError('기록 완료 영업일은 미래가 아니어야 하며 판매 기록이 1개 이상 필요합니다.')
    return s


def analysis_frame(s):
    names = {m['id']: m['name'] for m in s['menus']}
    rows = []
    for r in s['sales']:
        d = s['days'].get(r['date'], {})
        if d.get('status') == '영업' and d.get('complete') and parse_date(r['date']) <= date.today():
            row = dict(r)
            row.update(menu=names[r['id']], revenue=r['qty'] * r['price'],
                       sold_cost=None if r['cost'] is None else r['qty'] * r['cost'],
                       waste_cost=None if r['cost'] is None else r['waste'] * r['cost'],
                       weekday=WEEK[parse_date(r['date']).weekday()])
            rows.append(row)
    return pd.DataFrame(rows, columns=['id', 'date', 'qty', 'price', 'cost', 'waste', 'out', 'lost', 'note',
                                       'menu', 'revenue', 'sold_cost', 'waste_cost', 'weekday'])


def recommend(s, ref, adjustment):
    # 기준일을 포함한 최근 28일 = 각 요일 최대 4회. 기준일 이후 자료는 절대 사용하지 않습니다.
    start = ref - timedelta(days=27)
    monday = ref - timedelta(days=ref.weekday()) + timedelta(days=7)
    f = analysis_frame(s)
    result = []
    for m in s['menus']:
        if not m['active']:
            continue
        for i in range(7):
            target = monday + timedelta(days=i)
            if s['days'].get(target.isoformat(), {}).get('status') == '휴무':
                result.append({'예정일': str(target), '요일': WEEK[i], '메뉴': m['name'], '관측일수': 0,
                               '참고준비량': None, '설명': '예정 휴무: 준비량을 추천하지 않아요.'})
                continue
            h = f[(f['id'] == m['id']) & (f['date'] >= str(start)) & (f['date'] <= str(ref)) & (f['weekday'] == WEEK[i])]
            n = len(h)
            value = None
            text = f'최근 4주({start}~{ref}) 기록이 완료된 {WEEK[i]}요일 {n}회'
            if n < 2:
                text += ': 기록 부족 — 숫자 추천을 하지 않아요.'
            else:
                mean = float(h['qty'].mean())
                # Decimal로 100 × 1.1이 부동소수점 오차 때문에 111로 올림되는 문제를 방지합니다.
                exact_mean = Decimal(int(h['qty'].sum())) / Decimal(n)
                value = math.ceil(exact_mean * Decimal(100 + adjustment) / Decimal(100))
                text += f'의 {m["name"]} 평균 판매량은 {mean:,.2f}개예요. 조정률 {adjustment:+d}%를 적용하면 참고 준비량은 {value:,}개예요.'
                if n < 4:
                    text += ' 참고 자료 적음.'
            if n:
                out = int(h['out'].sum())
                waste = int(h['waste'].sum())
                known = h.loc[h['out'] & h['lost'].notna(), 'lost']
                unknown = int((h['out'] & h['lost'].isna()).sum())
                text += f' 폐기 {waste:,}개, 품절 {out}회, 놓친 주문 입력 합계 {int(known.sum()):,}개(미입력 {unknown}회).'
                if out:
                    text += ' 실제 수요가 판매량보다 많았을 수 있어요.'
                if waste:
                    text += ' 남은 수량을 함께 확인해 준비량을 조절하세요.'
            result.append({'예정일': str(target), '요일': WEEK[i], '메뉴': m['name'], '관측일수': n,
                           '참고준비량': value, '설명': text})
    return pd.DataFrame(result)


def example_store():
    # 모든 예시는 가상입니다. 실제 저장 공간과 완전히 분리합니다.
    import random
    rng = random.Random(42)
    s = empty_store()
    s['menus'] = [{'id': 'demo1', 'name': '김밥', 'price': 3500., 'cost': 1400., 'active': True},
                  {'id': 'demo2', 'name': '떡볶이', 'price': 4500., 'cost': 1800., 'active': True}]
    for ago in range(65, 0, -1):
        d = date.today() - timedelta(days=ago)
        if d.weekday() == 0:
            s['days'][str(d)] = {'status': '휴무', 'complete': False}
            continue
        if ago % 17 == 0:  # 미입력일도 예시에 포함
            continue
        s['days'][str(d)] = {'status': '영업', 'complete': True}
        for m in s['menus']:
            out = rng.random() < .18
            s['sales'].append({'id': m['id'], 'date': str(d), 'qty': rng.randint(15, 45),
                               'price': m['price'], 'cost': m['cost'], 'waste': rng.randint(0, 5),
                               'out': out, 'lost': None if out else 0, 'note': '가상 예시 기록'})
    return s


def plot_bar(df, x, y, title, color=None):
    enc = {'x': alt.X(x, title=None), 'y': alt.Y(y, title=None, sort='-x' if y == 'menu:N' else None), 'tooltip': list(df.columns)}
    if color:
        enc['color'] = alt.Color(color, scale=alt.Scale(scheme='greens'))
    chart = alt.Chart(df).mark_bar(color='#327452', cornerRadiusTopLeft=3).encode(**enc).properties(title=title)
    st.altair_chart(chart, use_container_width=True)


def main():
    st.set_page_config(page_title='다음 주 장사 도우미', page_icon='🌿', layout='wide')
    st.markdown('''<style>
    .stApp {background:#fcfaf5;color:#243c30;} h1,h2,h3 {color:#285c40;}
    .stButton button,.stDownloadButton button {min-height:3.1rem;font-size:1.05rem;border-radius:12px;}
    [data-testid="stMetric"] {background:white;padding:14px;border-radius:12px;border:1px solid #e4e9df;}
    [data-testid="stMetricValue"] {font-size:1.5rem;} p,label {font-size:1.02rem;}
    </style>''', unsafe_allow_html=True)
    st.title('🌿 부모님 가게의 다음 주 장사 도우미')
    st.caption('하루의 기록을 모아, 다음 주 준비를 조금 더 편하게')
    mode = st.radio('사용 데이터', ['실제 가게 기록', '예시 데이터 모드'], horizontal=True)
    key = 'demo_store' if mode == '예시 데이터 모드' else 'real_store'
    if key not in st.session_state:
        st.session_state[key] = example_store() if key == 'demo_store' else empty_store()
    s = st.session_state[key]
    revkey = key + '_revision'
    st.session_state.setdefault(revkey, 0)
    rev = st.session_state[revkey]
    def commit(new=None):
        if new is not None:
            st.session_state[key] = new
        st.session_state[revkey] += 1
        st.rerun()
    if key == 'demo_store':
        st.warning('가상의 예시 데이터입니다. 실제 가게 분석이 아니며 실제 기록과 분리되어 있습니다.')
    st.info('현재 접속 중에만 기록을 유지합니다. 저장 후 반드시 CSV를 다운로드하세요. 새로고침·접속 종료·앱 재시작 시 사라질 수 있습니다. 자동 영구 저장·가족 간 실시간 공유 기능은 없습니다.')

    f = analysis_frame(s)
    c1, c2 = st.columns(2)
    default_start = date.today() - timedelta(days=27)
    begin = c1.date_input('분석 시작일', default_start, key='begin')
    end = c2.date_input('분석 종료일', date.today(), key='end')
    if begin > end:
        st.error('시작일이 종료일보다 늦습니다.')
    period = f[(f['date'] >= str(begin)) & (f['date'] <= str(end))] if begin <= end else f.iloc[0:0]
    days = sum(str(begin) <= ds <= str(end) and d['status'] == '영업' and d['complete'] and ds <= str(date.today()) for ds, d in s['days'].items()) if begin <= end else 0
    st.caption(f'집계 기간 {begin}~{end} · 기록 완료 영업일 {days}일 · 미입력/휴무/작성 중 제외')
    unknown = period['cost'].isna().any()
    metrics = st.columns(5)
    values = [('총판매 수량', f'{int(period["qty"].sum()):,}개'),
              ('총매출', f'{period["revenue"].sum():,.0f}원')]
    for title, col in [('판매분 재료비', 'sold_cost'), ('폐기 재료비', 'waste_cost')]:
        values.append((title, '계산 불가' if unknown else f'{period[col].sum():,.0f}원'))
    balance = period['revenue'].sum() - period['sold_cost'].sum() - period['waste_cost'].sum()
    values.append(('재료비 차감 잔액', '계산 불가' if unknown else f'{balance:,.0f}원'))
    for column, (label, value) in zip(metrics, values):
        column.metric(label, value if len(period) else '자료 없음')
    st.caption('재료비 차감 잔액 = 매출 − 판매분 재료비 − 폐기 재료비. 임대료·인건비·세금·수수료 등을 반영하지 않아 순이익이 아닙니다.')
    if unknown:
        st.warning(f'개당 재료비 미입력 기록 {int(period["cost"].isna().sum())}건: 관련 재료비와 잔액은 계산 불가입니다. 0원으로 대체하지 않았습니다.')

    with st.expander('📁 기록 다운로드 · 기존 CSV 불러오기', expanded=True):
        st.download_button('⬇ 기록 다운로드 (전체 CSV)', export_csv(s),
                           f'{"가상예시" if key == "demo_store" else "가게기록"}_{date.today()}.csv', 'text/csv', use_container_width=True)
        st.caption('메뉴·날짜별 상태·전체 판매 기록이 UTF-8 BOM CSV 한 파일에 들어갑니다. 실제/예시 파일 이름을 구분하세요. 업로드는 현재 모드의 기록 전체를 교체합니다. 여러 CSV 병합은 지원하지 않습니다.')
        upload = st.file_uploader('이 앱에서 내려받은 CSV 선택', type=['csv'], key=f'upload_{key}_{rev}')
        replace = st.checkbox('현재 모드의 기록 전체를 업로드 파일로 교체합니다', key=f'replace_{key}_{rev}')
        if st.button('CSV 검증 후 불러오기', disabled=upload is None or not replace):
            try:
                imported = import_csv(upload.getvalue())
            except Exception as e:
                st.error(f'불러오기 실패: {e} · 기존 기록은 그대로입니다.')
            else:
                commit(imported)

    with st.expander('⚙ 메뉴 등록·수정 / 사용 중지', expanded=not bool(s['menus'])):
        st.caption('기본 가격/재료비 변경은 기존 판매 기록에 영향을 주지 않습니다. 판매 기록의 금액은 해당 날짜의 값으로 보존됩니다. 판매를 중단한 메뉴는 사용 중지를 선택하세요.')
        options = ['새 메뉴 등록'] + [m['id'] for m in s['menus']]
        names = {m['id']: m['name'] for m in s['menus']}
        chosen = st.selectbox('수정할 메뉴', options, format_func=lambda x: names.get(x, x), key=f'mselect_{key}')
        m = next((m for m in s['menus'] if m['id'] == chosen), None)
        with st.form(f'menu_{key}_{chosen}_{rev}'):
            name = st.text_input('메뉴명', value=m['name'] if m else '')
            price = st.number_input('기본 판매 단가 (원)', min_value=0., value=float(m['price']) if m else 0., step=100.)
            cost = st.number_input('1개당 재료비 (원, 모르면 빈칸)', min_value=0., value=m['cost'] if m else None, step=100.)
            active = st.checkbox('사용 중', value=m['active'] if m else True)
            if st.form_submit_button('메뉴 저장', use_container_width=True):
                if not name.strip() or any(x['name'] == name.strip() and x['id'] != chosen for x in s['menus']):
                    st.error('메뉴명을 입력하세요. 다른 메뉴와 이름이 중복되면 안 됩니다.')
                else:
                    try:
                        new = {'id': m['id'] if m else uuid.uuid4().hex, 'name': name.strip(),
                               'price': number(price, '단가'), 'cost': number(cost, '재료비', optional=True), 'active': active}
                        if m:
                            s['menus'][s['menus'].index(m)] = new
                        else:
                            s['menus'].append(new)
                        commit()
                    except ValueError as e:
                        st.error(str(e))

    st.header('✍ 기록 입력')
    st.caption('아래 표는 휴대폰에서 좌우로 밀어 확인할 수 있습니다. 빈 판매량은 미입력이며, 실제 0개 판매는 숫자 0을 입력하세요.')
    entry = st.date_input('기록할 영업일 / 예정 휴무일', date.today(), key=f'entry_{key}')
    ds = str(entry)
    day = s['days'].get(ds, {'status': '영업', 'complete': False})
    status = st.radio('날짜 상태', ['영업', '휴무'], index=0 if day['status'] == '영업' else 1,
                      horizontal=True, key=f'status_{key}_{ds}_{rev}')
    existing = {r['id']: r for r in s['sales'] if r['date'] == ds}
    if existing:
        st.warning('이 날짜의 기존 기록이 있습니다. 아래 값은 기존 값입니다. 저장하면 같은 날짜·메뉴 기록을 교체하며 중복 추가하지 않습니다.')
    if status == '휴무':
        agree = st.checkbox('기존 판매 기록이 있다면 삭제하고 휴무로 바꾸겠습니다', key=f'close_{key}_{ds}_{rev}')
        if st.button('휴무 저장', disabled=bool(existing) and not agree, use_container_width=True):
            s['sales'] = [r for r in s['sales'] if r['date'] != ds]
            s['days'][ds] = {'status': '휴무', 'complete': False}
            commit()
    elif entry > date.today():
        st.info('미래 날짜는 휴무 예정만 저장할 수 있습니다. 판매 기록은 오늘까지 입력하세요.')
    else:
        menus = [m for m in s['menus'] if m['active'] or m['id'] in existing]
        if not menus:
            st.info('먼저 위의 메뉴 설정에서 메뉴를 등록하세요.')
        else:
            rows = []
            for m in menus:
                r = existing.get(m['id'])
                rows.append({'메뉴ID': m['id'], '메뉴명': m['name'], '판매 수량': r['qty'] if r else None,
                             '판매 단가': r['price'] if r else m['price'], '개당 재료비': r['cost'] if r else m['cost'],
                             '폐기 수량': r['waste'] if r else 0, '품절 여부': r['out'] if r else False,
                             '놓친 예상 주문': r['lost'] if r else None, '특이사항': r['note'] if r else ''})
            editor_df = pd.DataFrame(rows)
            # 모두 빈 열이어도 숫자 편집기가 정상 동작하도록 명시적으로 숫자형을 지정합니다.
            for col in ['판매 수량', '판매 단가', '개당 재료비', '폐기 수량', '놓친 예상 주문']:
                editor_df[col] = pd.to_numeric(editor_df[col], errors='coerce').astype(float)
            with st.form(f'daily_{key}_{ds}_{rev}'):
                edited = st.data_editor(editor_df, hide_index=True, use_container_width=True,
                    disabled=['메뉴ID', '메뉴명'], column_config={
                        '메뉴ID': None,
                        '판매 수량': st.column_config.NumberColumn('판매 수량 (개)', min_value=0, step=1, format='%d'),
                        '판매 단가': st.column_config.NumberColumn('판매 단가 (원)', min_value=0., step=100.),
                        '개당 재료비': st.column_config.NumberColumn('개당 재료비 (원)', min_value=0., step=100.),
                        '폐기 수량': st.column_config.NumberColumn('폐기 수량 (개)', min_value=0, step=1, format='%d'),
                        '품절 여부': st.column_config.CheckboxColumn('품절 여부'),
                        '놓친 예상 주문': st.column_config.NumberColumn('놓친 예상 주문 (개·선택)', min_value=0, step=1, format='%d')})
                complete = st.checkbox('오늘 입력 완료 — 모든 표시 메뉴의 판매 수량을 확인했습니다', value=day['complete'])
                st.caption('판매 수량이 빈 행은 저장하지 않습니다. 완료하려면 모든 표시 메뉴의 판매량(0개 포함)이 필요합니다. 재료비는 모르면 빈칸으로 둘 수 있으나 비용 집계는 계산 불가로 표시됩니다. 놓친 주문 빈칸은 0건과 다릅니다.')
                overwrite = st.checkbox('기존 날짜 기록을 위 표로 교체하는 데 동의합니다', value=False) if existing else True
                save = st.form_submit_button('✅ 기록 입력 저장', use_container_width=True, type='primary')
            if save:
                try:
                    if not overwrite:
                        raise ValueError('기존 기록 교체에 동의해 주세요.')
                    newrows = []
                    for _, r in edited.iterrows():
                        qty = number(r['판매 수량'], f'{r["메뉴명"]} 판매 수량', True, optional=not complete)
                        if qty is None:
                            # 기존 행을 빈칸으로 지워 삭제하는 일은 전용 확인 절차로만 허용합니다.
                            if r['메뉴ID'] in existing:
                                raise ValueError('기존 기록 삭제는 아래 삭제 확인 절차를 사용하세요.')
                            continue
                        out = bool(r['품절 여부'])
                        lost = number(r['놓친 예상 주문'], '놓친 예상 주문', True, True)
                        if not out and lost not in [None, 0]:
                            raise ValueError('놓친 예상 주문이 있으면 품절 여부를 체크하세요.')
                        newrows.append({'id': r['메뉴ID'], 'date': ds, 'qty': qty,
                                        'price': number(r['판매 단가'], '판매 단가'),
                                        'cost': number(r['개당 재료비'], '개당 재료비', optional=True),
                                        'waste': number(r['폐기 수량'], '폐기 수량', True),
                                        'out': out, 'lost': lost, 'note': '' if missing(r['특이사항']) else str(r['특이사항'])})
                    s['sales'] = [r for r in s['sales'] if r['date'] != ds] + newrows
                    s['days'][ds] = {'status': '영업', 'complete': complete}
                    commit()
                except ValueError as e:
                    st.error(str(e))
    with st.expander('날짜별 상태 확인 / 기록 삭제'):
        st.caption('표에 없는 날짜는 미입력입니다. 영업·작성 중은 분석에서 제외됩니다. 완료일에도 특정 메뉴 기록이 없다면 그 메뉴의 판매량을 0으로 만들지 않고 관측에서 제외합니다.')
        if s['days']:
            st.dataframe(pd.DataFrame([{'날짜': d, '상태': v['status'], '기록': '완료' if v['complete'] else ('해당 없음' if v['status'] == '휴무' else '작성 중')} for d, v in sorted(s['days'].items(), reverse=True)]), hide_index=True, use_container_width=True)
            delete_day = st.selectbox('삭제할 날짜', sorted(s['days'], reverse=True), key=f'delday_{key}')
            menu_names = {m['id']: m['name'] for m in s['menus']}
            deletion = st.selectbox('삭제 범위', ['날짜 전체'] + [r['id'] for r in s['sales'] if r['date'] == delete_day],
                                     format_func=lambda x: menu_names.get(x, x), key=f'delrange_{key}_{delete_day}')
            confirmed = st.checkbox('삭제하면 되돌릴 수 없음을 확인했습니다', key=f'confirm_{key}_{rev}')
            if st.button('선택 기록 삭제', disabled=not confirmed):
                s['sales'] = [r for r in s['sales'] if not (r['date'] == delete_day and (deletion == '날짜 전체' or r['id'] == deletion))]
                if deletion == '날짜 전체':
                    del s['days'][delete_day]
                else:
                    s['days'][delete_day]['complete'] = False
                commit()

    st.header('📊 판매 흐름 살펴보기')
    if period.empty:
        st.info('선택 기간에 기록 완료된 영업일의 판매 기록이 없습니다. 실제 기록이 없을 때 예시 분석을 대신 표시하지 않습니다.')
    else:
        rank = period.groupby('menu', as_index=False).agg(판매수량=('qty', 'sum'), 매출=('revenue', 'sum'), 관측일수=('date', 'nunique'))
        plot_bar(rank, '판매수량:Q', 'menu:N', '메뉴별 판매 수량 순위 (개)')
        plot_bar(rank, '매출:Q', 'menu:N', '메뉴별 매출 순위 (원)')
        daily = period.groupby('date', as_index=False)['revenue'].sum()
        # 미입력 날짜를 잇지 않고 null로 두어, 0원으로 오해하거나 선이 연결되지 않게 합니다.
        timeline = pd.DataFrame({'date': pd.date_range(begin, min(end, date.today())).strftime('%Y-%m-%d')})
        daily = timeline.merge(daily, on='date', how='left')
        daily['segment'] = daily['revenue'].isna().cumsum().astype(str)
        st.altair_chart(alt.Chart(daily.dropna(subset=['revenue'])).mark_line(point=True, color='#327452').encode(
            x=alt.X('date:T', title='날짜'), y=alt.Y('revenue:Q', title='매출 (원)'), detail='segment:N', tooltip=['date:T', 'revenue:Q']).properties(title='날짜별 매출 변화 — 빈 날짜는 미입력/휴무/작성 중'), use_container_width=True)
        with st.expander('메뉴별 요일 평균 / 폐기·품절 / 상세 집계', expanded=True):
            weekday = period.groupby(['menu', 'weekday'], as_index=False).agg(판매합계=('qty', 'sum'), 관측일수=('date', 'nunique'))
            weekday['평균판매량'] = weekday['판매합계'] / weekday['관측일수']
            st.caption('요일 평균 = 해당 메뉴 판매량 합계 ÷ 해당 메뉴 기록이 있는 완료 영업일 수. 누락 메뉴를 0개로 채우지 않습니다. 메뉴마다 관측일수가 다를 수 있습니다.')
            st.altair_chart(alt.Chart(weekday).mark_bar().encode(x=alt.X('weekday:N', sort=WEEK, title='요일'), y=alt.Y('평균판매량:Q', title='평균 판매량 (개)'), color=alt.Color('menu:N', title='메뉴', scale=alt.Scale(scheme='greens')), xOffset='menu:N', tooltip=['menu', 'weekday', '판매합계', '관측일수', '평균판매량']), use_container_width=True)
            st.dataframe(weekday.rename(columns={'menu': '메뉴', 'weekday': '요일'}), hide_index=True, use_container_width=True)
            waste = period.groupby('menu', as_index=False).agg(폐기수량=('waste', 'sum'), 품절횟수=('out', 'sum'), 관측일수=('date', 'nunique'))
            plot_bar(waste, '폐기수량:Q', 'menu:N', '메뉴별 폐기 수량 (개)')
            plot_bar(waste, '품절횟수:Q', 'menu:N', '메뉴별 품절 발생 (기록일 수)')
            st.dataframe(waste, hide_index=True, use_container_width=True)
            detail = period[['date', 'menu', 'qty', 'price', 'cost', 'waste', 'out', 'lost', 'note']].rename(columns={'date': '영업일', 'menu': '메뉴', 'qty': '판매(개)', 'price': '단가(원)', 'cost': '재료비(원/개)', 'waste': '폐기(개)', 'out': '품절 여부', 'lost': '놓친 주문(개)', 'note': '특이사항'})
            st.dataframe(detail, hide_index=True, use_container_width=True)
    with st.expander('📅 월별 판매 현황 (선택한 기간 안의 기록)'):
        st.info('계절성을 살펴보려면 보통 1년 이상 기록이 유리하며 여러 해를 비교하면 더 도움이 됩니다. 월 일부만 선택하면 해당 월 일부만 집계합니다. 다음 달 판매량을 단정하지 않습니다.')
        if not period.empty:
            monthly = period.copy()
            monthly['월'] = monthly['date'].str[:7]
            monthly = monthly.groupby('월', as_index=False).agg(총판매수량=('qty', 'sum'), 총매출=('revenue', 'sum'), 완료영업일수=('date', 'nunique'))
            monthly['영업일당평균매출'] = monthly['총매출'] / monthly['완료영업일수']
            monthly['영업일당평균수량'] = monthly['총판매수량'] / monthly['완료영업일수']
            st.dataframe(monthly, hide_index=True, use_container_width=True,
                         column_config={c: st.column_config.NumberColumn(c + (' (원)' if '매출' in c else ' (개)'), format='localized') for c in ['총판매수량', '총매출', '영업일당평균매출', '영업일당평균수량']})
            plot_bar(monthly, '월:O', '총매출:Q', '월별 총매출 (원)')
        else:
            st.caption('분석할 기록이 없습니다.')

    st.header('🌱 다음 주 추천')
    ref = st.date_input('추천 기준일', date.today(), max_value=date.today(), key='reference')
    adjustment = st.slider('준비량 조정률 (%)', -20, 30, 0, step=1)
    st.caption('조정률은 통계적으로 검증된 값이 아닌 사용자의 운영 가정입니다. 기준일 포함 최근 28일의 같은 요일 최대 4회를 사용합니다. 위 분석 기간 선택과는 독립적입니다. 폐기나 놓친 주문은 자동 가산·차감하지 않습니다.')
    rec = recommend(s, ref, adjustment)
    if rec.empty:
        st.info('사용 중인 메뉴를 등록하면 추천을 확인할 수 있습니다.')
    else:
        display = rec.copy()
        display['참고준비량'] = display['참고준비량'].apply(lambda v: '—' if pd.isna(v) else f'{int(v):,}개')
        st.dataframe(display.drop(columns='설명'), hide_index=True, use_container_width=True)
        with st.expander('추천 이유 · 관측 횟수 · 품절과 폐기 참고', expanded=True):
            chosen_menu = st.selectbox('설명할 메뉴', rec['메뉴'].unique(), key=f'explain_{key}')
            for _, row in rec[rec['메뉴'] == chosen_menu].iterrows():
                st.markdown(f'**{row["예정일"]} ({row["요일"]})** — {row["설명"]}')
    st.warning('참고 준비량은 확정 예측이 아닙니다. 날씨·행사·가격 변화 등을 모두 반영하지 못합니다. 부모님의 경험과 예약 현황을 함께 확인하세요. 예정 휴무는 위 기록 입력에서 미래 날짜를 선택해 휴무로 저장하세요.')

    with st.expander('📱 배포 주소로 QR코드 만들기 / 보안 안내'):
        address = st.text_input('배포한 앱의 https 주소', placeholder='https://your-app.streamlit.app')
        if address:
            parsed = urlparse(address.strip())
            if parsed.scheme not in ['https', 'http'] or not parsed.netloc or parsed.username or parsed.password:
                st.error('계정 정보가 없는 올바른 http/https 앱 주소를 입력하세요.')
            else:
                image = qrcode.make(address.strip())
                buf = io.BytesIO()
                image.save(buf, format='PNG')
                st.image(buf.getvalue(), width=240)
                st.download_button('QR코드 PNG 다운로드', buf.getvalue(), '가게앱_QR.png', 'image/png')
        st.caption('QR코드는 주소를 담는 수단이며 보안 기능이 아닙니다. 계산대 안쪽에 붙여 스마트폰 카메라로 스캔하세요.')
        st.warning('이 버전 자체에는 로그인·접근 제한 기능이 없습니다. 링크를 아는 사람이 앱을 열 수 있습니다. 접속자별 세션은 별개이며 가족 간 데이터가 자동 공유되지 않습니다. 로그인 없는 앱에 민감한 자료를 올리지 마세요.')
        st.markdown('실제 매출 CSV, 개인정보, 비밀번호, API 키는 공개 GitHub에 올리지 마세요. 저장소에는 app.py와 requirements.txt만 올리세요. 실제 운영용 인증·권한 관리와 외부 데이터베이스/저장소 연동은 별도 확장이 필요합니다. CSV는 신뢰할 수 있는 기기에 백업하세요.')


if __name__ == '__main__':
    main()

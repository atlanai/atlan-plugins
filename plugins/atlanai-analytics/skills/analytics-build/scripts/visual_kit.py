"""The Atlan app's colours and icons, and the mini card's styles, as small static CSS for chat and the side view.

Every colour is a token with a light and a dark value; the host's colour scheme picks one. A card carries
only the tokens it uses: every character is an output token the viewer waits for.

Icon paths are from Hugeicons free (stroke rounded), the set the app uses, under the MIT
License: Copyright (c) 2025 Hugeicons. Permission is hereby granted, free of charge, to any
person obtaining a copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
to permit persons to whom the Software is furnished to do so, subject to the following
conditions: The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT
WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from html import escape
import re

# The app's design tokens: light, dark.
TOKENS = {
    'bg': ('#fff', '#202020'),
    'sb': ('#00000005', '#ffffff0f'),
    'mu': ('#00000014', '#ffffff14'),
    'bd': ('#e5e5e5', '#363636'),
    'bc': ('#00000017', '#ffffff1a'),
    'bh': ('#ccc', '#707070'),
    'tx': ('#191919', '#f7f7f7'),
    'ts': ('#636363', '#adadad'),
    'pf': ('#191919', '#f7f7f7'),
    'po': ('#fff', '#191919'),
    'bf': ('#0968f6', '#0968f6'),
    'bt': ('#0049b8', '#84b4fb'),
    'bm': ('#d4e5fe', '#0968f638'),
    'sx': ('#1f6c2a', '#4ce160'),
    'sm': ('#e0fae0', '#3cc14e33'),
    'sk': ('#3cc14e', '#4ce160'),
    'wx': ('#855f00', '#ffbd14'),
    'wm': ('#fff8d5', '#eebb042e'),
    'cx': ('#b60708', '#ffa0a0'),
    'ax': ('#3b1fc6', '#ad9efa'),
    'am': ('#e2ddfd', '#583aee47'),
    'c1': ('#0968f6', '#4d93fc'),
    'c6': ('#f02d2d', '#ff5c5c'),
    'e2': ('0 6px 6px -3px #0000000a,0 3px 3px -1.5px #0000000a,0 1px 1px -.5px #0000000a', '0 1px 2px #00000052'),
}

ICONS = {
    'metric': '<path d="M21 21H10C6.70017 21 5.05025 21 4.02513 19.9749C3 18.9497 3 17.2998 3 14V3"/><path d="M5 20C5.43938 16.8438 7.67642 8.7643 10.4282 8.7643C12.3301 8.7643 12.8226 12.6353 14.6864 12.6353C17.8931 12.6353 17.4282 4 21 4"/>',
    'sparkles': '<path d="M15 2L15.5387 4.39157C15.9957 6.42015 17.5798 8.00431 19.6084 8.46127L22 9L19.6084 9.53873C17.5798 9.99569 15.9957 11.5798 15.5387 13.6084L15 16L14.4613 13.6084C14.0043 11.5798 12.4202 9.99569 10.3916 9.53873L8 9L10.3916 8.46127C12.4201 8.00431 14.0043 6.42015 14.4613 4.39158L15 2Z"/>',
    'check-circle': '<path d="M22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22C17.5228 22 22 17.5228 22 12Z"/><path d="M8 12.75C8 12.75 9.6 13.6625 10.4 15C10.4 15 12.8 9.75 16 8"/>',
    'alert-circle': '<circle cx="12" cy="12" r="10"/><path d="M12 8V12"/><path d="M12.125 15.75H12M12.25 15.75C12.25 15.8881 12.1381 16 12 16C11.8619 16 11.75 15.8881 11.75 15.75C11.75 15.6119 11.8619 15.5 12 15.5C12.1381 15.5 12.25 15.6119 12.25 15.75Z"/>',
    'edit': '<path d="M16.4249 4.60509L17.4149 3.6151C18.2351 2.79497 19.5648 2.79497 20.3849 3.6151C21.205 4.43524 21.205 5.76493 20.3849 6.58507L19.3949 7.57506M16.4249 4.60509L9.76558 11.2644C9.25807 11.772 8.89804 12.4078 8.72397 13.1041L8 16L10.8959 15.276C11.5922 15.102 12.228 14.7419 12.7356 14.2344L19.3949 7.57506M16.4249 4.60509L19.3949 7.57506"/><path d="M18.9999 13.5C18.9999 16.7875 18.9999 18.4312 18.092 19.5376C17.9258 19.7401 17.7401 19.9258 17.5375 20.092C16.4312 21 14.7874 21 11.4999 21H11C7.22876 21 5.34316 21 4.17159 19.8284C3.00003 18.6569 3 16.7712 3 13V12.5C3 9.21252 3 7.56879 3.90794 6.46244C4.07417 6.2599 4.2599 6.07417 4.46244 5.90794C5.56879 5 7.21252 5 10.5 5"/>',
    'chart': '<path d="M8 9V17"/><path d="M13 5V17"/><path d="M18 13V17"/><path d="M3 3V13C3 16.7712 3 18.6569 4.17157 19.8284C5.34315 21 7.22876 21 11 21H21"/>',
    'database': '<path d="M3 12C3 7.75736 3 5.63604 4.31802 4.31802C5.63604 3 7.75736 3 12 3C16.2426 3 18.364 3 19.682 4.31802C21 5.63604 21 7.75736 21 12C21 16.2426 21 18.364 19.682 19.682C18.364 21 16.2426 21 12 21C7.75736 21 5.63604 21 4.31802 19.682C3 18.364 3 16.2426 3 12Z"/><path d="M3 12H21"/><path d="M11 7.5L17 7.5"/><path d="M7.125 7.5H7M7.25 7.5C7.25 7.63807 7.13807 7.75 7 7.75C6.86193 7.75 6.75 7.63807 6.75 7.5C6.75 7.36193 6.86193 7.25 7 7.25C7.13807 7.25 7.25 7.36193 7.25 7.5Z"/><path d="M11 16.5L17 16.5"/><path d="M7.125 16.5H7M7.25 16.5C7.25 16.6381 7.13807 16.75 7 16.75C6.86193 16.75 6.75 16.6381 6.75 16.5C6.75 16.3619 6.86193 16.25 7 16.25C7.13807 16.25 7.25 16.3619 7.25 16.5Z"/>',
    'arrow-up-right': '<path d="M9 6.65032C9 6.65032 15.9383 6.10759 16.9154 7.08463C17.8924 8.06167 17.3496 15 17.3496 15M16.5 7.5L6.5 17.5"/>',
    'layers': '<path d="M8.64298 3.14559L6.93816 3.93362C4.31272 5.14719 3 5.75397 3 6.75C3 7.74603 4.31272 8.35281 6.93817 9.56638L8.64298 10.3544C10.2952 11.1181 11.1214 11.5 12 11.5C12.8786 11.5 13.7048 11.1181 15.357 10.3544L17.0618 9.56638C19.6873 8.35281 21 7.74603 21 6.75C21 5.75397 19.6873 5.14719 17.0618 3.93362L15.357 3.14559C13.7048 2.38186 12.8786 2 12 2C11.1214 2 10.2952 2.38186 8.64298 3.14559Z"/><path d="M20.788 11.0972C20.9293 11.2959 21 11.5031 21 11.7309C21 12.7127 19.6873 13.3109 17.0618 14.5072L15.357 15.284C13.7048 16.0368 12.8786 16.4133 12 16.4133C11.1214 16.4133 10.2952 16.0368 8.64298 15.284L6.93817 14.5072C4.31272 13.3109 3 12.7127 3 11.7309C3 11.5031 3.07067 11.2959 3.212 11.0972"/><path d="M20.3767 16.2661C20.7922 16.5971 21 16.927 21 17.3176C21 18.2995 19.6873 18.8976 17.0618 20.0939L15.357 20.8707C13.7048 21.6236 12.8786 22 12 22C11.1214 22 10.2952 21.6236 8.64298 20.8707L6.93817 20.0939C4.31272 18.8976 3 18.2995 3 17.3176C3 16.927 3.20778 16.5971 3.62334 16.2661"/>',
}

# The mini card: a label row, the number, a line under it, one small picture, then chips and notes.
CARD = (
    '.mc{background:var(--bg);color:var(--tx);border-radius:14px;outline:1px solid var(--bc);'
    'padding:14px 16px;display:grid;gap:6px;font:14px/20px Geist,system-ui,-apple-system,sans-serif;max-width:560px}'
    '.mc svg{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}'
    '.mh{display:flex;align-items:center;gap:6px;font-size:13px}.mh b{font-weight:500}.mh svg{color:var(--ts)}'
    '.mp{font-size:13px;color:var(--ts)}'
    '.mv{font-weight:600;font-size:26px;line-height:32px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}.mv.neg{color:var(--cx)}'
    '.mv s{color:var(--ts);text-decoration:none;font-weight:400}'
    '.mt{font-size:13px;color:var(--ts)}.mt b{color:var(--tx);font-weight:500}'
    '.mb{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr) auto;gap:4px 8px;align-items:center;margin:4px 0;font-size:12px}.mr{display:contents}'
    '.mr>span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ts)}'
    '.z{position:relative;height:6px}.z i{position:absolute;top:0;height:6px;border-radius:3px;background:var(--c1)}.z i.n{background:var(--c6)}'
    '.mr b{font-weight:500;font-variant-numeric:tabular-nums;text-align:right}'
    '.mr.h>span:first-child,.mr.h b{color:var(--tx);font-weight:600}'
    '.my{font-size:13px}.mu{font-size:12px;line-height:16px;color:var(--ts);overflow-wrap:anywhere}'
    '.mf{font-size:12px;line-height:16px;color:var(--ts)}.mf .add{color:var(--sx)}'
    '.mw{display:flex;gap:6px;align-items:flex-start;font-size:12px;line-height:16px;color:var(--wx);background:var(--wm);border-radius:8px;padding:6px 8px}'
)


# The chip as a widget, the C3 ticker: a ring or a spark, the number, then what it counts.
TICKER = (
    '.c3{display:inline-flex;align-items:center;gap:10px;padding:6px 12px;border-radius:12px;background:var(--sb);'
    'outline:.5px solid var(--bd);color:var(--tx);font:13px/18px Geist,system-ui,-apple-system,sans-serif;max-width:100%}'
    '.c3 .n{font-weight:600;font-size:15px;font-variant-numeric:tabular-nums;white-space:nowrap}.c3 .d{color:var(--ts)}'
    '.rg{width:18px;height:18px;border-radius:50%;flex:none;-webkit-mask:radial-gradient(farthest-side,transparent 52%,#000 56%);'
    'mask:radial-gradient(farthest-side,transparent 52%,#000 56%)}'
    '.sk{display:inline-flex;align-items:flex-end;gap:2px;height:16px;flex:none}.sk i{width:4px;border-radius:1px;background:var(--c1)}'
    '.sk i.n{background:var(--c6)}'
)


def e(text):
    return escape(str(text), quote=True)


def icon(name):
    return '<svg viewBox="0 0 24 24" aria-hidden="true">%s</svg>' % ICONS[name]


def themes(names, scope='.a'):
    """The tokens, each one value the colour scheme picks from; an explicit light or dark choice wins."""
    values = ''.join('--%s:%s;' % (t, TOKENS[t][0] if TOKENS[t][0] == TOKENS[t][1] or t == 'e2' else 'light-dark(%s,%s)' % TOKENS[t])
                     for t in names)
    return ('%s{color-scheme:light dark;%s}' % (scope, values) + ':root[data-theme=dark] .a,:root[data-mode=dark] .a,.dark .a{color-scheme:dark}'
            ':root[data-theme=light] .a,:root[data-mode=light] .a{color-scheme:light}')


def ticker(body, summary):
    """A chip widget with only the tokens its rules and markup use."""
    names = sorted({t for t in re.findall(r'var\(--(\w\w)\)', TICKER + body) if t in TOKENS})
    return '<style>%s%s</style><div class="a c3" role="note" aria-label="%s">%s</div>' % (themes(names), TICKER, e(summary), body)


def card(body, summary):
    """A mini card with only the tokens its rules and markup use."""
    names = sorted({t for t in re.findall(r'var\(--(\w\w)\)', CARD + body) if t in TOKENS})
    return '<style>%s%s</style><div class="a mc" role="group" aria-label="%s">%s</div>' % (themes(names), CARD, e(summary), body)

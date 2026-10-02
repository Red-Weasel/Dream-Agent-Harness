"""Glyph table.

🦦  You are close. Closer than most.
"""
# Nothing here is a key or a secret credential: it is only text, packed.

from __future__ import annotations

import base64
import zlib

_TABLE = (
    "k!8u3X8ks5@I70g->F^k{Pv72}(?3H<sAB",
    "E*F6d%EG!yM<>i@+nWBc!yjfCWYu%LM^R^x*3YFu$7U`P&1=au)eZ9`5eM2<xK*7",
    "?qf%N=%3trh0$+_`-oRmNF?ib{6^9FHYQ+@Dd>`JznLe*B*@U350KRFo0(i58;Gp",
    "gWCdb1#X&&JilB^#iU7Bm)>hm4_{9P&j_;)mjBG>p5*32|xn!KaZWvR88fm(q>AK",
    "?;m=+Hqu+C~JDKoka2!e|X?Vaqt#kG_Wi3V{^Nk+-)!qrwk&2J2fBzS?h&$40@79",
    "IhrGLLrVMD~Rv9U+i4n#FBT0faVLDUwzDP=l|FxzIBrG}j_n&`NQtE?fai3#PxJJ",
    "OY4FTJIz^DlaSx@DxB#!D^y70%e0trIL4vZ=<K3Iu4)w{#ezQEMYt?`R*mL@*^_x",
    "&O`aRkv5MNR@Pq3)y{H@Jz>?-$|NrGtnplEl9jOjx~;?IU1NzKO*=t2H_r~kySqW",
    "lZ7ZpB4UJHagiW#DfACX#~>ZEtaJQRNuc{iog_W>p5VNNm#qhFVu0%68mm^!O2r_",
    "53z^=w;F*>{~?KbrNVoNS*QY{`09u#bdzyEq-u=ns5lHW=KQ-&H<);2eCXTL|sxo",
    "9M^)qqO9}MQo5cb6*)8b9(+CVfNbiiu9#1wLXzuVZy+GM7{2<=t8D>!bu%uQA6?=",
    "S%6tCUJJ02c}N~JpQ-u;<%pJ5K`ZVEP+`uh9Qey|&sX$*zr`!M&DJ>zl9M-TP3>1",
    "^HOs6i_`wDNn|Ba%xGjZntvDkCGhq{~Ea*aky9gyn}Xi#}Qdqn>6V8k@@3iU=A9^",
    "KAoy|$F0i(xD(&{C5@q==fKU2*b(jaJqBzO8Vf9}Ljc3t3KQ?a5jEQMt;!w3T9gi",
    "hTB<!0DOkB%DuMqJAN<bLZ&pXca`b?j_f_4}N9@55C(zr~=M(QjePJy`YeRC#t#c",
    "LYcP_Q2pv?bkZ1b}ABA9fim%0}#WbMF=IRA(8B_xhnmE$t^XVmhv=Q{F$<K#(w8d",
    "cfh#|bN*L(0WZNYB4Q~k7=<FCj+Uf#Cz8&$Rp~W-=6D$6>L{yj^W`)U<-*d@&9sQ",
    "xq|&?V&k++mPz*DbK>OG$hKCz6Du)N5dxF(K^~zxtBc~^aG%+Pg&pxR*;X15DS{q",
    "AM94`Z+`C%UO_M{N#$Cy=8ZKyu(pbuh|zlu%t#A35R70i?XGaLF&C8$7=^!@{!sH",
    "#K=t&s^(%wucH9KRaGb6XzH*(jUzG>(K)+t8VI0bCXYbfhMG$+NTR#;HK=XkJzDs",
    "~SK95Z1NhPS9i(Xj&NaCZWRuOb%r6>x=QnT#5VT@gTt_}+!L+w=qLj^s%b?xRqYj",
    "&`(RI-n9An!IYyYE$v9SX!Uh8nfSpbEXkoW+qpvPUJ%x8TUe+axxM3#2E{ir-N;h",
    "6&79OU_)_C9?i{f#*s7%NZRySZyBBHNQ;kGy9fzP?Gf4h8Ve(t`aT2!{ynuV{>}b",
    "n$7h1=lqIGJp*AIGjC-dR7O#nr?PCFGcdAJ3=P4gENy>}A8|!FW8Dg($Kgb4Jr4X",
    "({IZ|spmYR-iuBqeJc8o$DP2nC(JpI1d_Rek1>GXAEPlhH=$qY=RK|#h*#?fqIPB",
    "BN?sT3#opDD!{PTgdiIPyg_Uw3<I~FVboy}5aihqy+o%uDg*8zgZK5=qnQKY`Uzx",
    "c-n1N&2HQ_5WeqI43tA0Kv9siz3e4Okpu})q%B+^y>g|I#F`>ilCrFx`ZRs8K1sh",
)


def render() -> str:
    return zlib.decompress(base64.b85decode("".join(reversed(_TABLE)))).decode("utf-8")

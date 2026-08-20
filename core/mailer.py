# -*- coding: utf-8 -*-
"""邮件发送：SMTP 支持 SSL / STARTTLS，带占位符个性化。"""
import re
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


def personalize(template, lead, settings, lang="zh"):
    _self = settings.get("sender_name", "") or settings.get("company_name", "")
    _company = settings.get("company_name", "")
    if lang != "zh":
        if "采购" in _self:
            _self = _DEPT_LANG.get(lang, _self)
        if _company:
            _company = _COMPANY_EN
    vals = {
        "公司": lead.get("name", ""),
        "公司名": lead.get("name", ""),
        "联系人": lead.get("contact", "") or "客户",
        "称呼": lead.get("contact", "").split("先生")[0].split("女士")[0] or "客户",
        "地区": lead.get("region", ""),
        "产品": (settings.get("product_name_en") or settings.get("product_name", "光纤产品")) if lang != "zh" else settings.get("product_name", "光纤产品"),
        "自己": _self,
        "我方公司": _company,
    }
    out = template
    for k, v in vals.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def send_one(settings, to_addr, subject, body):
    """发送单封邮件，返回 (ok, error)。"""
    host = (settings.get("smtp_host") or "").strip()
    if not host:
        return False, "未配置 SMTP 服务器地址（请在“设置”里填写）"
    user = (settings.get("smtp_user") or "").strip()
    password = (settings.get("smtp_password") or "").strip()
    from_addr = (settings.get("from_addr") or "").strip() or user
    cc_addr = (settings.get("cc_addr") or "").strip()
    try:
        port = int(settings.get("smtp_port") or 465)
    except ValueError:
        port = 465
    ssl_mode = settings.get("smtp_ssl", "1") == "1"
    sender_name = (settings.get("sender_name") or settings.get("company_name") or "").strip()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(sender_name, "utf-8")), from_addr)) if sender_name else from_addr
    msg["To"] = to_addr
    if cc_addr:
        msg["Cc"] = cc_addr
    try:
        # local_hostname 固定为 localhost：避免 Windows 中文主机名导致 EHLO 编码失败
        if ssl_mode:
            server = smtplib.SMTP_SSL(host, port, timeout=20, local_hostname="localhost")
        else:
            server = smtplib.SMTP(host, port, timeout=20, local_hostname="localhost")
            server.starttls()
        server.login(user, password)
        recipients = [to_addr] + ([cc_addr] if cc_addr else [])
        server.sendmail(user, recipients, msg.as_string())
        server.quit()
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, "邮箱账号或密码不正确"
    except Exception as e:
        return False, str(e)


def validate_settings(settings):
    missing = []
    if not settings.get("smtp_host"):
        missing.append("SMTP 服务器")
    if not settings.get("smtp_user"):
        missing.append("发信邮箱")
    if not settings.get("smtp_password"):
        missing.append("邮箱授权码/密码")
    return missing


# ---------- 多语言自动识别与模板 ----------

_LANGS = {"zh", "en", "es", "fr", "de", "ru", "ja", "ko", "ar", "pt"}

_LANG_NAME_TO_CODE = {
    "中文": "zh", "zh": "zh", "chinese": "zh",
    "英文": "en", "en": "en", "english": "en",
    "西班牙": "es", "es": "es", "spanish": "es", "esp": "es",
    "法文": "fr", "法语": "fr", "fr": "fr", "french": "fr",
    "德文": "de", "德语": "de", "de": "de", "german": "de",
    "俄文": "ru", "俄语": "ru", "ru": "ru", "russian": "ru",
    "日文": "ja", "日语": "ja", "ja": "ja", "japanese": "ja",
    "韩文": "ko", "韩语": "ko", "ko": "ko", "korean": "ko",
    "阿拉伯": "ar", "ar": "ar", "arabic": "ar",
    "葡萄牙": "pt", "葡语": "pt", "pt": "pt", "portuguese": "pt",
}

_CN_REGION_KEYWORDS = [
    "中国", "大陆", "香港", "澳门", "台湾", "北京", "上海", "天津", "重庆",
    "广东", "深圳", "广州", "东莞", "佛山", "珠海", "惠州", "中山", "江门", "肇庆", "汕头",
    "江苏", "南京", "苏州", "无锡", "常州", "南通", "扬州", "镇江",
    "浙江", "杭州", "宁波", "温州", "嘉兴", "绍兴", "金华", "义乌", "台州",
    "山东", "济南", "青岛", "烟台", "潍坊", "临沂",
    "福建", "福州", "厦门", "泉州", "漳州",
    "四川", "成都", "绵阳", "湖北", "武汉", "宜昌", "湖南", "长沙", "株洲",
    "河南", "郑州", "洛阳", "河北", "石家庄", "廊坊", "保定", "唐山",
    "陕西", "西安", "安徽", "合肥", "芜湖", "江西", "南昌", "赣州",
    "辽宁", "沈阳", "大连", "吉林", "长春", "黑龙江", "哈尔滨",
    "山西", "太原", "云南", "昆明", "贵州", "贵阳", "广西", "南宁", "桂林",
    "海南", "海口", "三亚", "内蒙古", "呼和浩特", "宁夏", "银川", "甘肃", "兰州",
    "青海", "西宁", "新疆", "乌鲁木齐", "西藏", "拉萨",
]

_COUNTRY_LANG = {
    "中国": "zh", "china": "zh",
    "美国": "en", "usa": "en", "united states": "en", "us": "en",
    "英国": "en", "uk": "en", "united kingdom": "en", "britain": "en",
    "加拿大": "en", "canada": "en",
    "澳大利亚": "en", "australia": "en",
    "新西兰": "en", "new zealand": "en",
    "新加坡": "en", "singapore": "en",
    "印度": "en", "india": "en",
    "菲律宾": "en", "philippines": "en",
    "马来西亚": "en", "malaysia": "en",
    "尼日利亚": "en", "nigeria": "en",
    "肯尼亚": "en", "kenya": "en",
    "南非": "en", "south africa": "en",
    "加纳": "en", "ghana": "en",
    "爱尔兰": "en", "ireland": "en",
    "德国": "de", "germany": "de", "deutschland": "de",
    "奥地利": "de", "austria": "de",
    "瑞士": "de", "switzerland": "de",
    "西班牙": "es", "spain": "es", "espana": "es",
    "墨西哥": "es", "mexico": "es",
    "阿根廷": "es", "argentina": "es",
    "哥伦比亚": "es", "colombia": "es",
    "智利": "es", "chile": "es",
    "秘鲁": "es", "peru": "es",
    "厄瓜多尔": "es", "ecuador": "es",
    "委内瑞拉": "es", "venezuela": "es",
    "乌拉圭": "es", "uruguay": "es",
    "巴拉圭": "es", "paraguay": "es",
    "玻利维亚": "es", "bolivia": "es",
    "危地马拉": "es", "guatemala": "es",
    "古巴": "es", "cuba": "es",
    "巴拿马": "es", "panama": "es",
    "多米尼加": "es", "dominican republic": "es",
    "哥斯达黎加": "es", "costa rica": "es",
    "萨尔瓦多": "es", "el salvador": "es",
    "洪都拉斯": "es", "honduras": "es",
    "尼加拉瓜": "es", "nicaragua": "es",
    "法国": "fr", "france": "fr",
    "比利时": "fr", "belgium": "fr",
    "卢森堡": "fr", "luxembourg": "fr",
    "摩纳哥": "fr", "monaco": "fr",
    "塞内加尔": "fr", "senegal": "fr",
    "科特迪瓦": "fr", "cote d'ivoire": "fr", "ivory coast": "fr",
    "喀麦隆": "fr", "cameroon": "fr",
    "阿尔及利亚": "fr", "algeria": "fr",
    "突尼斯": "fr", "tunisia": "fr",
    "摩洛哥": "fr", "morocco": "fr",
    "俄罗斯": "ru", "russia": "ru",
    "白俄罗斯": "ru", "belarus": "ru",
    "哈萨克斯坦": "ru", "kazakhstan": "ru",
    "日本": "ja", "japan": "ja",
    "韩国": "ko", "south korea": "ko", "korea": "ko",
    "巴西": "pt", "brazil": "pt",
    "葡萄牙": "pt", "portugal": "pt",
    "安哥拉": "pt", "angola": "pt",
    "莫桑比克": "pt", "mozambique": "pt",
    "沙特阿拉伯": "ar", "saudi arabia": "ar",
    "阿联酋": "ar", "united arab emirates": "ar", "uae": "ar",
    "埃及": "ar", "egypt": "ar",
    "卡塔尔": "ar", "qatar": "ar",
    "科威特": "ar", "kuwait": "ar",
    "巴林": "ar", "bahrain": "ar",
    "阿曼": "ar", "oman": "ar",
    "约旦": "ar", "jordan": "ar",
    "黎巴嫩": "ar", "lebanon": "ar",
    "伊拉克": "ar", "iraq": "ar",
    "也门": "ar", "yemen": "ar",
}

_TLD_LANG = {
    "cn": "zh", "hk": "zh", "tw": "zh", "mo": "zh",
    "de": "de", "at": "de", "ch": "de",
    "jp": "ja", "kr": "ko",
    "fr": "fr", "be": "fr", "lu": "fr", "mc": "fr",
    "es": "es", "mx": "es", "ar": "es", "cl": "es", "co": "es", "pe": "es",
    "br": "pt", "pt": "pt",
    "ru": "ru", "kz": "ru", "by": "ru",
    "sa": "ar", "ae": "ar", "eg": "ar", "qa": "ar", "kw": "ar", "bh": "ar", "om": "ar", "jo": "ar", "lb": "ar", "iq": "ar", "ye": "ar",
}




_DEPT_LANG = {
    "en": "Purchasing Department", "es": "Departamento de Compras", "fr": "Service des Achats",
    "de": "Einkaufsabteilung", "ru": "Отдел закупок", "ja": "購買部", "ko": "구매부",
    "ar": "قسم المشتريات", "pt": "Departamento de Compras",
}

_COMPANY_EN = "Yishan Technology Co., Ltd."
def detect_lang(lead):
    """根据线索的国家/地区/邮箱判断邮件语言，返回 zh/en/es/fr/de/ru/ja/ko/ar/pt。"""
    lang = str(lead.get("lang") or "").strip().lower()
    if lang:
        if lang in _LANGS:
            return lang
        for k, v in _LANG_NAME_TO_CODE.items():
            if k in lang or lang in k:
                return v
    country = str(lead.get("country") or "").strip().lower()
    region = str(lead.get("region") or "").strip().lower()
    for text in (country, region):
        if not text:
            continue
        if any(k.lower() in text for k in _CN_REGION_KEYWORDS):
            return "zh"
        for key, lc in _COUNTRY_LANG.items():
            if key in text or text in key:
                return lc
    email = str(lead.get("email") or "")
    if "@" in email:
        tld = email.rsplit(".", 1)[-1].lower()
        if tld in _TLD_LANG:
            return _TLD_LANG[tld]
    if country:
        return "en"
    if region and any("\u4e00" <= ch <= "\u9fff" for ch in region):
        return "zh"
    return "zh"


_LANG_TPL = {
    "zh": {
        "subject": "关于贵司{{产品}}的询价",
        "body": "{{联系人}}您好：\n\n我是{{我方公司}}的{{自己}}，目前正在筛选{{产品}}供应商，看到贵司产品线匹配，想了解以下信息：\n\n1. 常供型号及含税报价（不同起订量）\n2. 库存与交货周期\n3. 样品政策\n4. 资质证书（ISO/CE/RoHS）\n\n如方便，请发一份电子画册或报价单，收到后我会尽快确认。\n\n期待回复，谢谢！\n\n此致敬礼\n\nEason\n电话：18688962816\n{{我方公司}} {{自己}}",
    },
    "en": {
        "subject": "Inquiry about your {{产品}}",
        "body": "Dear Sir/Madam,\n\nI am writing on behalf of {{我方公司}}, {{自己}}. We are currently selecting suppliers for {{产品}} and noticed your product line matches our requirements. Could you please provide:\n\n1. Available models and tax-inclusive prices (by order quantity)\n2. Stock availability and lead time\n3. Sample policy\n4. Certificates (ISO/CE/RoHS)\n\nPlease feel free to send us your catalog or quotation. We will confirm shortly after receiving it.\n\nLooking forward to your reply.\nBest regards\n\nEason\nPhone: +86 186 8896 2816\nYishan Technology Co., Ltd. - Purchasing Department",
    },
    "es": {
        "subject": "Consulta sobre sus {{产品}}",
        "body": "Estimado/a señor/a:\n\nLe escribo en nombre de {{我方公司}} ({{自己}}). Estamos seleccionando proveedores de {{产品}} y su línea de productos coincide con nuestras necesidades. ¿Podrían indicarnos:\n\n1. Modelos disponibles y precios con impuestos (según cantidad de pedido)\n2. Disponibilidad de stock y plazo de entrega\n3. Política de muestras\n4. Certificados (ISO/CE/RoHS)\n\nSi es posible, envíennos su catálogo o cotización; lo confirmaremos rápidamente.\n\nQuedamos a la espera de su respuesta.\nSaludos cordiales\n\nEason\nTel: +86 186 8896 2816\nYishan Technology Co., Ltd. - Departamento de Compras",
    },
    "fr": {
        "subject": "Demande de devis pour vos {{产品}}",
        "body": "Madame, Monsieur,\n\nJe vous écris au nom de {{我方公司}} ({{自己}}). Nous recherchons actuellement des fournisseurs de {{产品}} et votre gamme correspond à nos besoins. Pourriez-vous nous communiquer :\n\n1. Les modèles disponibles et vos prix TTC (selon la quantité)\n2. La disponibilité en stock et les délais de livraison\n3. Votre politique d'échantillons\n4. Vos certificats (ISO/CE/RoHS)\n\nN'hésitez pas à nous envoyer votre catalogue ou un devis ; nous confirmerons rapidement.\n\nDans l'attente de votre réponse.\nCordialement\n\nEason\nTél : +86 186 8896 2816\nYishan Technology Co., Ltd. - Service des Achats",
    },
    "de": {
        "subject": "Anfrage zu Ihren {{产品}}",
        "body": "Sehr geehrte Damen und Herren,\n\nich schreibe Ihnen im Namen von {{我方公司}} ({{自己}}). Wir suchen derzeit Lieferanten für {{产品}} und Ihr Sortiment passt zu unseren Anforderungen. Könnten Sie uns bitte mitteilen:\n\n1. Verfügbare Modelle und Preise inkl. Steuern (nach Bestellmenge)\n2. Lagerverfügbarkeit und Lieferzeit\n3. Musterpolitik\n4. Zertifikate (ISO/CE/RoHS)\n\nGerne senden Sie uns Ihren Katalog oder ein Angebot; wir bestätigen schnellstmöglich.\n\nWir freuen uns auf Ihre Rückmeldung.\nMit freundlichen Grüßen\n\nEason\nTel: +86 186 8896 2816\nYishan Technology Co., Ltd. - Einkaufsabteilung",
    },
    "ru": {
        "subject": "Запрос о вашей продукции {{产品}}",
        "body": "Уважаемые господа,\n\nЯ пишу от имени компании {{我方公司}} ({{自己}}). Мы подбираем поставщиков продукции {{产品}}, и ваш ассортимент соответствует нашим требованиям. Просим сообщить:\n\n1. Доступные модели и цены с налогами (в зависимости от объёма заказа)\n2. Наличие на складе и сроки поставки\n3. Условия предоставления образцов\n4. Сертификаты (ISO/CE/RoHS)\n\nПри возможности пришлите каталог или коммерческое предложение — мы быстро подтвердим получение.\n\nЖдём вашего ответа.\nС уважением\n\nEason\nТел: +86 186 8896 2816\nYishan Technology Co., Ltd. - Отдел закупок",
    },
    "ja": {
        "subject": "{{产品}}に関するお問い合わせ",
        "body": "拝啓\n\n{{我方公司}}の{{自己}}と申します。このたび{{产品}}の仕入れ先を検討しており、御社の製品ラインナップが当方のニーズに合致しております。以下の情報をご提供いただけますでしょうか。\n\n1. 取り扱いモデルと税込価格（注文数量別）\n2. 在庫状況と納期\n3. サンプル提供の条件\n4. 各種認証（ISO/CE/RoHS）\n\nカタログや見積書をお送りいただければ幸いです。確認次第、速やかにご返答いたします。\n\nご返信お待ちしております。\n敬具\n\nEason\n電話: +86 186 8896 2816\n易山科技有限公司 購買部",
    },
    "ko": {
        "subject": "{{产品}} 견적 문의",
        "body": "안녕하세요.\n\n저는 {{我方公司}}의 {{自己}}입니다. 현재 {{产品}} 공급업체를 선정 중이며, 귀사의 제품 라인이 저희 요구에 부합하여 연락드립니다. 다음 정보를 알려주시기 바랍니다.\n\n1. 취급 모델 및 세금 포함 가격(주문 수량별)\n2. 재고 현황 및 납기\n3. 샘플 정책\n4. 인증서(ISO/CE/RoHS)\n\n가능하시면 카탈로그 또는 견적서를 보내주시기 바랍니다. 확인 후 빠르게 회신드리겠습니다.\n\n회신 기다리겠습니다.\n감사합니다.\n\nEason\n전화: +86 186 8896 2816\nYishan Technology Co., Ltd. - 구매부",
    },
    "ar": {
        "subject": "استفسار عن منتجاتكم {{产品}}",
        "body": "تحية طيبة وبعد،\n\nأكتب نيابة عن شركة {{我方公司}} ({{自己}}). نبحث حالياً عن موردين لمنتجات {{产品}}، وقد لاحظنا أن منتجاتكم تتوافق مع احتياجاتنا. نرجو تزويدنا بالمعلومات التالية:\n\n1. الموديلات المتوفرة والأسعار شاملة الضريبة (حسب كمية الطلب)\n2. توفر المخزون ومدة التسليم\n3. سياسة العينات\n4. الشهادات (ISO/CE/RoHS)\n\nيرجى إرسال الكتالوج أو عرض الأسعار إن أمكن، وسنؤكد الاستلام سريعاً.\n\nبانتظار ردكم.\nمع تحياتي\n\nEason\nهاتف: +86 186 8896 2816\nYishan Technology Co., Ltd. - قسم المشتريات",
    },
    "pt": {
        "subject": "Consulta sobre seus {{产品}}",
        "body": "Prezados,\n\nEscrevo em nome da {{我方公司}} ({{自己}}). Estamos selecionando fornecedores de {{产品}} e a sua linha de produtos atende às nossas necessidades. Poderiam informar:\n\n1. Modelos disponíveis e preços com impostos (conforme a quantidade)\n2. Disponibilidade em estoque e prazo de entrega\n3. Política de amostras\n4. Certificados (ISO/CE/RoHS)\n\nSe possível, enviem o catálogo ou cotação; confirmaremos rapidamente.\n\nAguardamos seu retorno.\nAtenciosamente\n\nEason\nTel: +86 186 8896 2816\nYishan Technology Co., Ltd. - Departamento de Compras",
    },
}


def localize(subject, body, lang):
    """按语言返回内置模板；没有对应语言时返回原模板。"""
    tpl = _LANG_TPL.get(lang)
    if not tpl:
        return subject, body
    return tpl["subject"], tpl["body"]
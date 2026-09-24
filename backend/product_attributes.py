"""Public technical characteristics only. Preserve supplier units and scales.

The supplied GetGoodsInfo output schema (2026-09-17) has no verified country of
manufacture field. Do not infer it from a brand, article or manufacturer code,
or add guessed country aliases here. Until an explicit per-product source is
verified, the storefront describes the country as requiring confirmation.
"""
FIELDS={
    'tires': [('width','Ширина'),('height','Профиль'),('diameter','Диаметр'),('constr','Конструкция'),
              ('load_index','Индекс нагрузки'),('speed_index','Индекс скорости'),('tonnage','Усиление'),
              ('thorn','Шипы'),('puncture','Защита от прокола'),('noise','Шумность'),('comfort','Комфорт'),
              ('grip','Сцепление'),('aquaplaning','Устойчивость к аквапланированию'),
              ('passability','Проходимость'),('softness','Мягкость'),('wear_index','Индекс износостойкости'),
              ('initial_tread_depth','Глубина протектора'),('protector_type','Тип протектора'),
              ('axle','Ось установки'),('number_layers_treadmill','Количество слоёв протектора'),
              ('camera','Камерность'),('omolog','Омологация')],
    'wheels':[('width','Ширина диска'),('diameter','Диаметр'),('et','Вылет ET'),('dia','Центральное отверстие DIA'),
              ('bolts_count','Количество крепёжных отверстий'),('bolts_spacing','PCD'),('color','Цвет'),('mount_note','Особенности крепления')],
    'tubes':[('diameter','Диаметр'),('sub_type_name','Тип')],
    'sensors':[('weight','Масса')],
    'oils':[('sae','Вязкость SAE'),('acea','ACEA'),('api','API'),('composition','Состав'),('fuel_type','Тип топлива'),('apply','Применение'),('volume','Объём, л')],
    'consumables':[('sub_type','Тип'),('sub_type_name','Тип'),('diameter','Диаметр')],
}

def product_attributes(kind, detail):
    result=[];seen=set()
    for key,label in FIELDS.get(kind,[]):
        value=detail.get(key)
        if value is None or isinstance(value,(dict,list,tuple)):continue
        if key in ('wear_index','initial_tread_depth','number_layers_treadmill'):
            try:
                if float(value)<=0:continue
            except (ValueError,TypeError):continue
        value=('Да' if value else 'Нет') if type(value) is bool else str(value).strip()
        value=''.join(c for c in value if c.isprintable())[:400]
        if value in ('','-','—') or label in seen:continue
        result.append({'label':label,'value':value});seen.add(label)
    return result

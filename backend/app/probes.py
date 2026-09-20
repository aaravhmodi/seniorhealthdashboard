"""Choose the next question after a check-in.

The question is deliberately separate from the action ladder: it helps collect
the next useful fact, but it never changes the safety level by itself.
"""
from __future__ import annotations

from .schemas import FollowUp, RiskConcern


_QUESTIONS: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "head_bleed": ("Did you hit your head or pass out, and are you taking a blood thinner?", "Your answer helps us tell a simple fall from a possible head injury."),
        "fracture": ("Can you put weight on it, and is anything bent, numb, or badly swollen?", "Your answer helps us judge how urgently the injury needs to be seen."),
        "cardiac": ("Are you having chest pressure, shortness of breath, sweating, or nausea right now?", "Your answer helps us check for warning signs that need urgent attention."),
        "stroke": ("Did the weakness or speech trouble start suddenly, and is it on one side?", "The timing and one-sided pattern are important emergency warning signs."),
        "infection_delirium": ("Do you have a fever, burning when you urinate, or new confusion?", "Those details help separate a mild symptom from an infection needing care."),
        "breathing": ("Are you short of breath at rest, and can you speak a full sentence?", "Your answer helps us judge how much the breathing problem is affecting you now."),
        "sepsis": ("Do you have a fever, shaking chills, feel faint, or feel newly confused?", "These details help us look for signs of a serious infection."),
        "medication_effect": ("When did this start relative to your last dose, and did anything change?", "The timing helps a pharmacist decide whether a medicine could be contributing."),
        "dehydration": ("Have you been able to drink and urinate normally today?", "Your answer helps us check whether dehydration could be making this worse."),
        "spinal_cord": ("Do you have leg weakness or numbness, trouble walking, or trouble controlling your bladder or bowels?", "Those symptoms would make back pain more urgent."),
        "generic": ("What else should I know: when did it start, and is it getting better or worse?", "Your answer helps us understand the timing and whether anything is changing."),
    },
    "es": {
        "head_bleed": ("\u00bfSe golpe\u00f3 la cabeza o se desmay\u00f3, y est\u00e1 tomando un anticoagulante?", "Su respuesta nos ayuda a distinguir una ca\u00edda simple de una posible lesi\u00f3n en la cabeza."),
        "fracture": ("\u00bfPuede apoyar peso en esa parte, y hay algo torcido, adormecido o muy hinchado?", "Su respuesta nos ayuda a saber con qu\u00e9 urgencia debe revisarse la lesi\u00f3n."),
        "cardiac": ("\u00bfTiene presi\u00f3n en el pecho, falta de aire, sudoraci\u00f3n o n\u00e1useas ahora?", "Su respuesta nos ayuda a revisar se\u00f1ales de alarma que requieren atenci\u00f3n urgente."),
        "stroke": ("\u00bfLa debilidad o dificultad para hablar comenz\u00f3 de repente y est\u00e1 en un solo lado?", "El momento y el hecho de que sea de un solo lado son se\u00f1ales de alarma importantes."),
        "infection_delirium": ("\u00bfTiene fiebre, ardor al orinar o confusi\u00f3n nueva?", "Esos detalles ayudan a distinguir un s\u00edntoma leve de una infecci\u00f3n que necesita atenci\u00f3n."),
        "breathing": ("\u00bfLe falta el aire en reposo y puede decir una frase completa?", "Su respuesta nos ayuda a saber cu\u00e1nto le est\u00e1 afectando ahora el problema para respirar."),
        "sepsis": ("\u00bfTiene fiebre, escalofr\u00edos fuertes, se siente a punto de desmayarse o est\u00e1 confundido de repente?", "Estos detalles nos ayudan a buscar se\u00f1ales de una infecci\u00f3n grave."),
        "medication_effect": ("\u00bfCu\u00e1ndo comenz\u00f3 en relaci\u00f3n con su \u00faltima dosis y cambi\u00f3 algo?", "El momento ayuda al farmac\u00e9utico a decidir si un medicamento podr\u00eda estar contribuyendo."),
        "dehydration": ("\u00bfHa podido beber l\u00edquidos y orinar normalmente hoy?", "Su respuesta nos ayuda a revisar si la deshidrataci\u00f3n podr\u00eda estar empeorando esto."),
        "spinal_cord": ("\u00bfTiene debilidad o adormecimiento en las piernas, dificultad para caminar o para controlar la vejiga o los intestinos?", "Esos s\u00edntomas har\u00edan m\u00e1s urgente el dolor de espalda."),
        "generic": ("\u00bfQu\u00e9 m\u00e1s debo saber: cu\u00e1ndo comenz\u00f3 y est\u00e1 mejorando o empeorando?", "Su respuesta nos ayuda a entender el momento en que comenz\u00f3 y si algo est\u00e1 cambiando."),
    },
    "pt": {
        "head_bleed": ("Voc\u00ea bateu a cabe\u00e7a ou desmaiou, e toma algum anticoagulante?", "Sua resposta nos ajuda a diferenciar uma queda simples de uma poss\u00edvel les\u00e3o na cabe\u00e7a."),
        "fracture": ("Voc\u00ea consegue apoiar peso nessa parte, e h\u00e1 algo torto, dormente ou muito inchado?", "Sua resposta nos ajuda a avaliar com que urg\u00eancia a les\u00e3o precisa ser examinada."),
        "cardiac": ("Voc\u00ea est\u00e1 com press\u00e3o no peito, falta de ar, suor ou n\u00e1usea agora?", "Sua resposta nos ajuda a verificar sinais de alerta que precisam de atendimento urgente."),
        "stroke": ("A fraqueza ou dificuldade para falar come\u00e7ou de repente e est\u00e1 em um lado s\u00f3?", "O momento e o padr\u00e3o de um lado s\u00f3 s\u00e3o sinais de alerta importantes."),
        "infection_delirium": ("Voc\u00ea est\u00e1 com febre, ardor ao urinar ou confus\u00e3o nova?", "Esses detalhes ajudam a diferenciar um sintoma leve de uma infec\u00e7\u00e3o que precisa de cuidado."),
        "breathing": ("Voc\u00ea est\u00e1 com falta de ar em repouso e consegue falar uma frase completa?", "Sua resposta nos ajuda a avaliar quanto o problema respirat\u00f3rio est\u00e1 afetando voc\u00ea agora."),
        "sepsis": ("Voc\u00ea est\u00e1 com febre, calafrios fortes, sentindo que vai desmaiar ou com confus\u00e3o nova?", "Esses detalhes ajudam a procurar sinais de uma infec\u00e7\u00e3o grave."),
        "medication_effect": ("Quando isso come\u00e7ou em rela\u00e7\u00e3o \u00e0 sua \u00faltima dose, e algo mudou?", "O momento ajuda o farmac\u00eautico a avaliar se um rem\u00e9dio pode estar contribuindo."),
        "dehydration": ("Voc\u00ea conseguiu beber l\u00edquidos e urinar normalmente hoje?", "Sua resposta nos ajuda a verificar se a desidrata\u00e7\u00e3o pode estar piorando isso."),
        "spinal_cord": ("Voc\u00ea tem fraqueza ou dorm\u00eancia nas pernas, dificuldade para andar ou para controlar a bexiga ou o intestino?", "Esses sintomas tornariam a dor nas costas mais urgente."),
        "generic": ("O que mais devo saber: quando come\u00e7ou e est\u00e1 melhorando ou piorando?", "Sua resposta nos ajuda a entender quando come\u00e7ou e se algo est\u00e1 mudando."),
    },
    "zh": {
        "head_bleed": ("您撞到头或晕倒了吗？您是否正在服用血液稀释剂？", "您的回答有助于我们区分普通跌倒和可能的头部受伤。"),
        "fracture": ("您能在这处承重吗？有没有变形、麻木或明显肿胀？", "您的回答有助于我们判断伤势需要多快就医。"),
        "cardiac": ("您现在有胸部压迫感、呼吸短促、出汗或恶心吗？", "您的回答有助于我们检查需要紧急处理的警示信号。"),
        "stroke": ("无力或说话困难是突然开始的吗？是在身体一侧吗？", "开始的时间和单侧表现是重要的急症警示信号。"),
        "infection_delirium": ("您有发烧、排尿时灼痛或新出现的意识混乱吗？", "这些信息有助于区分轻微症状和需要治疗的感染。"),
        "breathing": ("您在休息时也会呼吸困难吗？您能说完整的一句话吗？", "您的回答有助于我们判断呼吸问题现在对您的影响。"),
        "sepsis": ("您有发烧、剧烈发冷、感觉要晕倒或新出现的意识混乱吗？", "这些信息有助于我们寻找严重感染的迹象。"),
        "medication_effect": ("症状是在您上次服药后多久开始的？有什么改变吗？", "时间关系有助于药剂师判断药物是否可能造成影响。"),
        "dehydration": ("今天您能正常喝水和排尿吗？", "您的回答有助于我们检查脱水是否可能使情况变得更糟。"),
        "spinal_cord": ("您的腿是否无力或麻木、走路困难，或无法控制大小便？", "这些症状会让背痛变得更加紧急。"),
        "generic": ("还有什么需要告诉我：什么时候开始的？是在好转还是加重？", "您的回答有助于我们了解症状的时间和变化。"),
    },
    "hi": {
        "head_bleed": ("क्या आपके सिर पर चोट लगी या आप बेहोश हुए? क्या आप खून पतला करने वाली दवा लेते हैं?", "आपका जवाब साधारण गिरने और सिर की संभावित चोट में अंतर करने में मदद करता है।"),
        "fracture": ("क्या आप उस हिस्से पर वजन डाल सकते हैं? क्या वह टेढ़ा, सुन्न या बहुत सूजा हुआ है?", "आपका जवाब हमें चोट की तात्कालिकता समझने में मदद करता है।"),
        "cardiac": ("क्या अभी आपके सीने में दबाव, सांस फूलना, पसीना या मितली है?", "आपका जवाब हमें तुरंत ध्यान देने वाले चेतावनी संकेतों की जांच करने में मदद करता है।"),
        "stroke": ("क्या कमजोरी या बोलने में परेशानी अचानक शुरू हुई और शरीर के एक तरफ है?", "शुरुआत का समय और एक तरफ के लक्षण महत्वपूर्ण आपातकालीन संकेत हैं।"),
        "infection_delirium": ("क्या आपको बुखार, पेशाब करते समय जलन या नई उलझन है?", "ये बातें हल्के लक्षण और देखभाल की जरूरत वाले संक्रमण में अंतर करने में मदद करती हैं।"),
        "breathing": ("क्या आराम करते समय भी सांस फूलती है? क्या आप पूरा वाक्य बोल सकते हैं?", "आपका जवाब हमें समझने में मदद करता है कि सांस की समस्या अभी आपको कितना प्रभावित कर रही है।"),
        "sepsis": ("क्या आपको बुखार, तेज ठंड लगना, बेहोशी जैसा लगना या अचानक नई उलझन है?", "ये बातें गंभीर संक्रमण के संकेत खोजने में मदद करती हैं।"),
        "medication_effect": ("यह आपकी पिछली खुराक के कितनी देर बाद शुरू हुआ? क्या कुछ बदला था?", "समय का संबंध फार्मासिस्ट को यह समझने में मदद करता है कि दवा कारण हो सकती है या नहीं।"),
        "dehydration": ("क्या आज आप सामान्य रूप से पानी पी पा रहे हैं और पेशाब कर पा रहे हैं?", "आपका जवाब हमें जांचने में मदद करता है कि पानी की कमी इसे और खराब तो नहीं कर रही।"),
        "spinal_cord": ("क्या आपके पैरों में कमजोरी या सुन्नपन, चलने में परेशानी, या पेशाब या मल पर नियंत्रण में परेशानी है?", "ये लक्षण कमर दर्द को अधिक जरूरी बना देंगे।"),
        "generic": ("मुझे और क्या जानना चाहिए: यह कब शुरू हुआ, और बेहतर हो रहा है या बिगड़ रहा है?", "आपका जवाब हमें समय और बदलाव समझने में मदद करता है।"),
    },
}


def choose_follow_up(concerns: list[RiskConcern], language: str = "en") -> FollowUp:
    """Return one useful next question, never ``None``."""
    concern = concerns[0] if concerns else None
    catalog = _QUESTIONS.get(language, _QUESTIONS["en"])
    question, why = catalog.get(concern.code, catalog["generic"]) if concern else catalog["generic"]
    return FollowUp(
        code=f"probe:{concern.code}" if concern else "probe:generic",
        question=question,
        why=why,
        concern_code=concern.code if concern else None,
        concern_label=concern.label if concern else None,
        sharpens=1.0,
        opens=False,
        generic=concern is None,
    )


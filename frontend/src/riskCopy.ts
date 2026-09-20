import type { RiskBandName } from "./types";

export type RiskCopy = {
  whatCouldBe: string;
  noConcern: string;
  noConcernText: string;
  keepWatching: string;
  riskExplanation: string;
  otherThing: (count: number) => string;
  startsAt: string;
  thenMoves: string;
  onWhat: string;
  withComplaint: string;
  at: string;
  whyNumber: string;
  hideWorking: string;
  whatRaised: string;
  whereStarted: string;
  whatMoved: string;
  driverCaveat: string;
  measured: string;
  clinicalWeighting: string;
  whereNumbers: string;
  trainedModel: string;
  notLoaded: string;
  modelReads: string;
  overallRisk: string;
  howModel: string;
  trainingRecords: string;
  years: string;
  heldOutAuc: string;
  testedOn: string;
  keyedOn: string;
  modelCaveat: string;
  ladderNote: string;
  bandLabels: Record<RiskBandName, string>;
  bandActions: Record<RiskBandName, string>;
  concernLabels: Record<string, string>;
  concernPlain: Record<string, string>;
};

const en: RiskCopy = {
  whatCouldBe: "What might be causing this?",
  noConcern: "No clear warning sign found",
  noConcernText: "We did not find a clear warning sign in this check-in. Keep watching and answer the next question.",
  keepWatching: "Keep watching",
  riskExplanation: "This is a possible cause, not a diagnosis. The number is an estimate based on what you told us today.",
  otherThing: (count) => `, and ${count} other thing${count === 1 ? "" : "s"} worth naming`,
  startsAt: "Starts at",
  thenMoves: "then moves to",
  onWhat: "on what",
  withComplaint: "with this complaint",
  at: "At",
  whyNumber: "Why this number?",
  hideWorking: "Hide the working",
  whatRaised: "What raised it at all",
  whereStarted: "Where the number started",
  whatMoved: "What moved it, and by how much",
  driverCaveat: "Each figure is this check-in's percentage with that piece of evidence minus the percentage without it. They are not slices of a pie, so they do not add up to the total.",
  measured: "measured",
  clinicalWeighting: "clinical weighting",
  whereNumbers: "Where these numbers come from",
  trainedModel: "model trained on this",
  notLoaded: "not loaded here",
  modelReads: "The trained model reads this check-in at",
  overallRisk: "overall risk of needing admission or observation.",
  howModel: "How that model was trained",
  trainingRecords: "Training records",
  years: "Years",
  heldOutAuc: "Held-out AUC",
  testedOn: "Tested on",
  keyedOn: "What it keyed on in {possessive} words",
  modelCaveat: "These are the terms with the largest positive contribution to this score, read straight off the fitted model — not a guess about what it might have used.",
  ladderNote: "These percentages explain and rank. They never lower the recommendation; the safety rules set the floor and the action shown is the more urgent of the two.",
  bandLabels: { monitor: "Keep watching", today: "Get seen today", emergency: "Emergency department", now: "Call 911" },
  bandActions: { monitor: "Log it and check in again tomorrow. Tell us if it changes.", today: "Call your clinic or pharmacist today and describe this.", emergency: "Go to the emergency department now. Do not drive yourself.", now: "Call 911 now and stay where you are." },
  concernLabels: {
    head_bleed: "Bleeding inside the head", fracture: "A broken bone from the fall", cardiac: "A heart problem", stroke: "A stroke",
    infection_delirium: "An infection showing up as confusion", breathing: "A breathing problem", sepsis: "An infection spreading through the body",
    medication_effect: "One of your medicines causing this", dehydration: "Low blood pressure or dehydration", spinal_cord: "Pressure on the nerves in your back",
  },
  concernPlain: {},
};

const es: RiskCopy = {
  ...en,
  whatCouldBe: "¿Qué podría estar causando esto?", noConcern: "No encontramos una señal de alarma clara",
  noConcernText: "No encontramos una señal de alarma clara en este registro. Siga observando y responda la siguiente pregunta.",
  keepWatching: "Siga observando", riskExplanation: "Esto es una posible causa, no un diagnóstico. El número es una estimación basada en lo que nos contó hoy.",
  otherThing: (count) => ` y ${count} otra${count === 1 ? " cosa" : "s cosas"} que vale la pena mencionar`, startsAt: "Comienza en", thenMoves: "luego sube a", onWhat: "según lo que", at: "Al", whyNumber: "¿Por qué este número?", hideWorking: "Ocultar el detalle",
  whatRaised: "Qué lo activó", whereStarted: "Dónde comenzó el número", whatMoved: "Qué lo movió y cuánto", driverCaveat: "Cada cifra es el porcentaje de este registro con esa evidencia menos el porcentaje sin ella. No son partes de un pastel y no tienen que sumar el total.", measured: "medido", clinicalWeighting: "ponderación clínica", whereNumbers: "De dónde vienen estos números", trainedModel: "modelo entrenado con esto", notLoaded: "no cargado aquí", modelReads: "El modelo entrenado lee este registro en", overallRisk: "de riesgo general de necesitar ingreso u observación.", howModel: "Cómo se entrenó el modelo", trainingRecords: "Registros de entrenamiento", years: "Años", heldOutAuc: "AUC de prueba", testedOn: "Probado en", keyedOn: "En qué palabras se fijó", modelCaveat: "Estos son los términos que más contribuyeron a esta puntuación, directamente del modelo ajustado; no una suposición.", ladderNote: "Estos porcentajes explican y ordenan. Nunca reducen la recomendación; las reglas de seguridad establecen el mínimo.",
  bandLabels: { monitor: "Siga observando", today: "Consulte hoy", emergency: "Urgencias", now: "Llame al 911" },
  bandActions: { monitor: "Regístrelo y vuelva a consultar mañana. Avísenos si cambia.", today: "Llame hoy a su clínica o farmacéutico y descríbalo.", emergency: "Vaya ahora a urgencias. No conduzca usted mismo.", now: "Llame al 911 ahora y quédese donde está." },
  concernLabels: { head_bleed: "Sangrado dentro de la cabeza", fracture: "Una fractura por la caída", cardiac: "Un problema del corazón", stroke: "Un derrame cerebral", infection_delirium: "Una infección que aparece como confusión", breathing: "Un problema respiratorio", sepsis: "Una infección que se extiende por el cuerpo", medication_effect: "Uno de sus medicamentos puede causarlo", dehydration: "Presión baja o deshidratación", spinal_cord: "Presión sobre los nervios de la espalda" },
  concernPlain: { head_bleed: "Un golpe en la cabeza puede sangrar lentamente dentro del cráneo y comenzar horas después.", fracture: "Las caderas y muñecas pueden romperse fácilmente en una caída.", cardiac: "En adultos mayores, un infarto puede aparecer sin dolor en el pecho.", stroke: "La caída de un lado de la cara, debilidad del brazo o dificultad para hablar requieren actuar rápido.", infection_delirium: "En adultos mayores, una infección urinaria o del pecho puede aparecer primero como confusión.", breathing: "El líquido en los pulmones, una infección o un coágulo pueden comenzar con falta de aire.", sepsis: "Una infección puede extenderse rápidamente por el cuerpo y cambiar los signos vitales.", medication_effect: "Un medicamento puede estar causando el síntoma y un farmacéutico puede ayudar a revisarlo.", dehydration: "Es común y suele corregirse, pero también puede ser el comienzo de algo serio.", spinal_cord: "El dolor de espalda con debilidad, entumecimiento o problemas de vejiga no puede esperar." },
};

const pt: RiskCopy = {
  ...en,
  whatCouldBe: "O que pode estar causando isso?", noConcern: "Não encontramos um sinal de alerta claro", noConcernText: "Não encontramos um sinal de alerta claro neste registro. Continue observando e responda à próxima pergunta.", keepWatching: "Continue observando", riskExplanation: "Esta é uma possível causa, não um diagnóstico. O número é uma estimativa baseada no que você contou hoje.", otherThing: (count) => ` e mais ${count} ${count === 1 ? "ponto" : "pontos"} que vale a pena nomear`, startsAt: "Começa em", thenMoves: "depois vai para", onWhat: "com base no que", at: "Em", whyNumber: "Por que este número?", hideWorking: "Ocultar detalhes", whatRaised: "O que o ativou", whereStarted: "Onde o número começou", whatMoved: "O que mudou o número e quanto", driverCaveat: "Cada valor é a porcentagem deste registro com aquela evidência menos a porcentagem sem ela. Eles não são partes de um total e não precisam somar.", measured: "medido", clinicalWeighting: "ponderação clínica", whereNumbers: "De onde vêm estes números", trainedModel: "modelo treinado com isto", notLoaded: "não carregado aqui", modelReads: "O modelo treinado lê este registro em", overallRisk: "de risco geral de precisar de internação ou observação.", howModel: "Como o modelo foi treinado", trainingRecords: "Registros de treinamento", years: "Anos", heldOutAuc: "AUC de teste", testedOn: "Testado em", keyedOn: "Em quais palavras ele se baseou", modelCaveat: "Estes são os termos com maior contribuição positiva para esta pontuação, diretamente do modelo ajustado; não uma suposição.", ladderNote: "Estas porcentagens explicam e ordenam. Nunca reduzem a recomendação; as regras de segurança definem o mínimo.",
  bandLabels: { monitor: "Continue observando", today: "Procure atendimento hoje", emergency: "Pronto-socorro", now: "Ligue para 911" }, bandActions: { monitor: "Registre e faça novo check-in amanhã. Avise se mudar.", today: "Ligue hoje para sua clínica ou farmacêutico e descreva isso.", emergency: "Vá agora ao pronto-socorro. Não dirija.", now: "Ligue para 911 agora e permaneça onde está." },
  concernLabels: { head_bleed: "Sangramento dentro da cabeça", fracture: "Fratura causada pela queda", cardiac: "Um problema cardíaco", stroke: "Um AVC", infection_delirium: "Uma infecção aparecendo como confusão", breathing: "Um problema respiratório", sepsis: "Uma infecção se espalhando pelo corpo", medication_effect: "Um dos seus medicamentos causando isso", dehydration: "Pressão baixa ou desidratação", spinal_cord: "Pressão nos nervos das costas" },
  concernPlain: { head_bleed: "Uma pancada na cabeça pode sangrar lentamente dentro do crânio e começar horas depois.", fracture: "Quadris e pulsos podem quebrar facilmente em uma queda.", cardiac: "Em idosos, um ataque cardíaco pode acontecer sem dor no peito.", stroke: "Rosto caído, fraqueza no braço ou dificuldade para falar exigem ação rápida.", infection_delirium: "Em idosos, uma infecção urinária ou no peito pode aparecer primeiro como confusão.", breathing: "Líquido nos pulmões, infecção ou coágulo podem começar com falta de ar.", sepsis: "Uma infecção pode se espalhar rapidamente pelo corpo e alterar os sinais vitais.", medication_effect: "Um medicamento pode estar causando o sintoma e o farmacêutico pode ajudar a revisar.", dehydration: "É comum e geralmente corrigível, mas também pode ser o começo de algo sério.", spinal_cord: "Dor nas costas com fraqueza, dormência ou problema na bexiga não pode esperar." },
};

const zh: RiskCopy = {
  ...en,
  whatCouldBe: "可能是什么原因？", noConcern: "没有发现明显的警示信号", noConcernText: "这次记录没有发现明显的警示信号。请继续观察，并回答下一个问题。", keepWatching: "继续观察", riskExplanation: "这只是可能的原因，不是诊断。这个数字是根据您今天告诉我们的情况估算的。", otherThing: (count) => `，以及另外 ${count} 个值得说明的问题`, startsAt: "起点是", thenMoves: "然后变为", onWhat: "根据", at: "达到", whyNumber: "为什么是这个数字？", hideWorking: "隐藏计算过程", whatRaised: "是什么触发了它", whereStarted: "数字从哪里开始", whatMoved: "什么改变了数字，以及改变了多少", driverCaveat: "每个数字都是本次记录有这项证据时的百分比减去没有这项证据时的百分比。它们不是总数的分块，因此不会相加为总数。", measured: "测量数据", clinicalWeighting: "临床权重", whereNumbers: "这些数字来自哪里", trainedModel: "使用此数据训练的模型", notLoaded: "此处未加载", modelReads: "训练模型将本次记录评估为", overallRisk: "需要住院或留观的总体风险。", howModel: "模型如何训练", trainingRecords: "训练记录", years: "年份", heldOutAuc: "留出集 AUC", testedOn: "测试年份", keyedOn: "模型关注的用词", modelCaveat: "这些词对评分的正向贡献最大，直接来自训练模型，而不是猜测。", ladderNote: "这些百分比用于解释和排序，不会降低建议；安全规则决定最低行动级别。", bandLabels: { monitor: "继续观察", today: "今天就医", emergency: "急诊", now: "拨打 911" }, bandActions: { monitor: "记录下来，明天再次检查。如果变化，请告诉我们。", today: "今天联系诊所或药剂师并说明情况。", emergency: "现在去急诊。请不要自己开车。", now: "立即拨打 911，并留在原地。" },
  concernLabels: { head_bleed: "颅内出血", fracture: "跌倒造成的骨折", cardiac: "心脏问题", stroke: "中风", infection_delirium: "以意识混乱表现的感染", breathing: "呼吸问题", sepsis: "扩散到全身的感染", medication_effect: "某种药物可能导致了这个问题", dehydration: "低血压或脱水", spinal_cord: "背部神经受到压迫" },
  concernPlain: { head_bleed: "头部受到撞击后，颅内可能缓慢出血，数小时后才出现症状。", fracture: "跌倒时髋部和手腕很容易骨折。", cardiac: "老年人的心脏病发作有时不会出现胸痛。", stroke: "面部下垂、手臂无力或说话困难需要迅速处理。", infection_delirium: "老年人的尿路或胸部感染可能先表现为意识混乱。", breathing: "肺部积液、感染或血栓都可能从呼吸困难开始。", sepsis: "感染可能迅速扩散到全身并改变生命体征。", medication_effect: "某种药物可能导致症状，药剂师可以帮助检查。", dehydration: "这很常见，通常可以纠正，但也可能是严重问题的开始。", spinal_cord: "背痛伴随腿无力、麻木或膀胱问题时不能等待。" },
};

const hi: RiskCopy = {
  ...en,
  whatCouldBe: "इसका कारण क्या हो सकता है?", noConcern: "कोई स्पष्ट चेतावनी संकेत नहीं मिला", noConcernText: "इस जांच में कोई स्पष्ट चेतावनी संकेत नहीं मिला। निगरानी जारी रखें और अगले प्रश्न का उत्तर दें।", keepWatching: "निगरानी जारी रखें", riskExplanation: "यह केवल एक संभावित कारण है, निदान नहीं। यह संख्या आज आपके बताए अनुसार एक अनुमान है।", otherThing: (count) => ` और ${count} अन्य महत्वपूर्ण बात`, startsAt: "शुरुआत", thenMoves: "फिर बढ़कर", onWhat: "आपके बताए अनुसार", at: "इस स्तर पर", whyNumber: "यह संख्या क्यों?", hideWorking: "विवरण छिपाएँ", whatRaised: "इसे किसने सक्रिय किया", whereStarted: "संख्या कहाँ से शुरू हुई", whatMoved: "संख्या को किसने और कितना बदला", driverCaveat: "हर आंकड़ा इस जांच में उस प्रमाण के साथ प्रतिशत और उसके बिना प्रतिशत का अंतर है। ये हिस्से नहीं हैं, इसलिए इनका योग कुल प्रतिशत नहीं होगा।", measured: "मापा गया", clinicalWeighting: "चिकित्सीय आकलन", whereNumbers: "ये आंकड़े कहाँ से आते हैं", trainedModel: "इस पर प्रशिक्षित मॉडल", notLoaded: "यहाँ लोड नहीं है", modelReads: "प्रशिक्षित मॉडल इस जांच में", overallRisk: "अस्पताल में भर्ती या निगरानी की कुल संभावना पढ़ता है।", howModel: "मॉडल कैसे प्रशिक्षित हुआ", trainingRecords: "प्रशिक्षण रिकॉर्ड", years: "वर्ष", heldOutAuc: "परीक्षण AUC", testedOn: "परीक्षण वर्ष", keyedOn: "मॉडल ने किन शब्दों पर ध्यान दिया", modelCaveat: "ये वे शब्द हैं जिनका इस स्कोर में सबसे बड़ा सकारात्मक योगदान है, सीधे प्रशिक्षित मॉडल से; यह अनुमान नहीं है।", ladderNote: "ये प्रतिशत समझाने और क्रम तय करने के लिए हैं। ये सलाह को कम नहीं करते; सुरक्षा नियम न्यूनतम कार्रवाई तय करते हैं।", bandLabels: { monitor: "निगरानी जारी रखें", today: "आज दिखाएँ", emergency: "आपात विभाग", now: "911 पर कॉल करें" }, bandActions: { monitor: "इसे दर्ज करें और कल फिर जांच करें। बदलाव हो तो हमें बताएं।", today: "आज अपने क्लिनिक या फार्मासिस्ट को कॉल करके बताएं।", emergency: "अभी आपात विभाग जाएं। खुद गाड़ी न चलाएं।", now: "अभी 911 पर कॉल करें और वहीं रहें।" },
  concernLabels: { head_bleed: "सिर के अंदर रक्तस्राव", fracture: "गिरने से हड्डी टूटना", cardiac: "हृदय की समस्या", stroke: "स्ट्रोक", infection_delirium: "भ्रम के रूप में दिखने वाला संक्रमण", breathing: "सांस लेने की समस्या", sepsis: "पूरे शरीर में फैलता संक्रमण", medication_effect: "आपकी कोई दवा इसका कारण हो सकती है", dehydration: "कम रक्तचाप या पानी की कमी", spinal_cord: "पीठ की नसों पर दबाव" },
  concernPlain: { head_bleed: "सिर पर चोट के बाद खोपड़ी के अंदर धीरे-धीरे रक्तस्राव हो सकता है और यह घंटों बाद शुरू हो सकता है।", fracture: "गिरने पर कूल्हे और कलाई आसानी से टूट सकते हैं।", cardiac: "बड़ी उम्र में दिल का दौरा बिना सीने में दर्द के भी हो सकता है।", stroke: "चेहरा झुकना, हाथ में कमजोरी या बोलने में परेशानी पर तुरंत कार्रवाई चाहिए।", infection_delirium: "बड़ी उम्र में मूत्र या छाती का संक्रमण पहले भ्रम के रूप में दिख सकता है।", breathing: "फेफड़ों में पानी, संक्रमण या खून का थक्का सांस फूलने से शुरू हो सकता है।", sepsis: "संक्रमण तेजी से पूरे शरीर में फैल सकता है और महत्वपूर्ण संकेत बदल सकता है।", medication_effect: "कोई दवा लक्षण पैदा कर सकती है और फार्मासिस्ट इसकी जांच में मदद कर सकता है।", dehydration: "यह आमतौर पर ठीक हो जाता है, लेकिन गंभीर समस्या की शुरुआत भी हो सकती है।", spinal_cord: "कमजोरी, सुन्नपन या मूत्राशय की समस्या के साथ पीठ दर्द में इंतजार नहीं करना चाहिए।" },
};

const copies: Record<string, RiskCopy> = { en, es, pt, zh, hi };
export function getRiskCopy(language?: string): RiskCopy {
  return copies[language || "en"] || en;
}

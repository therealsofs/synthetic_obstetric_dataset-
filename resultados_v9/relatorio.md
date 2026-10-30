# Pré-validação do dataset materno

Dataset: coorte_sintetica_5000x162v9.csv; 5000 registos.
Estado técnico: SEM ERROS TÉCNICOS DETETADOS NO ÂMBITO EXECUTADO
Achados clínicos descritivos e desvios a recomendações não são automaticamente problemas de qualidade. As contagens por grupo não devem ser somadas. Revisão profissional pendente.

## Erros técnicos: 0 registos

Nenhuma ocorrência nas regras executadas deste grupo.


## Possíveis questões de qualidade para revisão: 0 registos

Nenhuma ocorrência nas regras executadas deste grupo.


## Desvios a recomendações: 4962 registos

- **Intervalo visit3 -> visit4 de 4-6 semanas (28-42 dias) [M21]**: 4644/4663 avaliações. Verifica se visit4_date − visit3_date fica entre 28 e 42 dias, inclusive.
- **Intervalo visit1 -> visit2 de 4-6 semanas (28-42 dias) [M19]**: 4099/4863 avaliações. Verifica se visit2_date − visit1_date fica entre 28 e 42 dias, inclusive.
- **Intervalo visit2 -> visit3 de 4-6 semanas (28-42 dias) [M20]**: 1903/4740 avaliações. Verifica se visit3_date − visit2_date fica entre 28 e 42 dias, inclusive.

## Achados clínicos descritivos: 4709 registos

- **IMC fora do intervalo normal recomendado na pré-conceção (18,5 a 25 kg/m2) [M15]**: 3316/5000 avaliações. Sinaliza bmi < 18.5 ou > 25 (limites incluídos no intervalo).
- **Glicemia inicial em jejum no intervalo de critério de DG [E_GLIC_INICIAL]**: 1695/4980 avaliações. Quando fasting_glucose < 126: sinaliza fasting_glucose >= 92.
- **Idade materna >=35 anos: risco acrescido de complicações na gravidez [M01]**: 1653/5000 avaliações. Sinaliza maternal_age >= 35.
- **Hemoglobina abaixo do limiar OMS no terceiro trimestre [E_HB_third]**: 1420/4943 avaliações. Sinaliza hemoglobin_third_trimester < 11.
- **Pressão arterial na semana 29: sistólica ≥140 ou diastólica ≥90 mmHg [E_PA_w29_ALTA]**: 1393/4913 avaliações. Sinaliza quando (blood_pressure_week29_systolic >= 140 OU blood_pressure_week29_diastolic >= 90).
- **Pressão arterial na repetição do terceiro trimestre: sistólica ≥140 ou diastólica ≥90 mmHg [E_PA_rep_ALTA]**: 1352/4913 avaliações. Sinaliza quando (repeated_third_trimester_blood_pressure_systolic >= 140 OU repeated_third_trimester_blood_pressure_diastolic >= 90).
- **Hemoglobina abaixo do limiar OMS no segundo trimestre [E_HB_second]**: 999/5000 avaliações. Sinaliza hemoglobin_second_trimester < 10.5.
- **Idade gestacional reportada inferior a 37 semanas [E_PRETERMO]**: 967/5000 avaliações. Sinaliza gestational_age_at_delivery_weeks < 37.
- **Peso ao nascer na categoria ≥4000 g [E_PESO_4000]**: 858/5000 avaliações. Sinaliza birth_weight_g >= 4000.
- **Hemoglobina abaixo do limiar OMS no primeiro trimestre [E_HB_first]**: 799/5000 avaliações. Sinaliza hemoglobin_first_trimester < 11.
- **Escala de Edimburgo >=12: referenciação a Psicologia/Psiquiatria aconselhada [M05]**: 787/5000 avaliações. Sinaliza EPDS_score >= 12.
- **Peso ao nascer inferior a 2500 g [E_BAIXO_PESO]**: 753/5000 avaliações. Sinaliza birth_weight_g < 2500.
- **Pressão arterial no pós-parto: sistólica ≥140 ou diastólica ≥90 mmHg [E_PA_pos_ALTA]**: 699/5000 avaliações. Sinaliza quando (postpartum_blood_pressure_systolic >= 140 OU postpartum_blood_pressure_diastolic >= 90).
- **Percentil de peso fetal estimado abaixo de 10 [E_EFW_P10]**: 670/4823 avaliações. Sinaliza estimated_fetal_weight_third_trimester_percentile < 10.
- **Proteinúria atinge 300 mg/24 h [E_PROTEINA]**: 489/4913 avaliações. Sinaliza third_trimester_24h_proteinuria_mg >= 300.
- **PA repetida elevada + proteinúria após 20 semanas: padrão a rever [E_PE_PARCIAL]**: 444/4902 avaliações. Sinaliza quando (dias_visit4_corrigida >= 140 E (repeated_third_trimester_blood_pressure_systolic >= 140 OU repeated_third_trimester_blood_pressure_diastolic >= 90) E third_trimester_24h_proteinuria_mg >= 300).
- **PTGO 0h: valor atinge limiar de hiperglicemia [E_PTGO_0h]**: 372/3241 avaliações. Quando OGTT_performed == 1: sinaliza OGTT_fasting_glucose >= 92.
- **Pressão arterial na semana 29: sistólica ≥160 ou diastólica ≥110 mmHg [E_PA_w29_GRAVE]**: 369/4913 avaliações. Sinaliza quando (blood_pressure_week29_systolic >= 160 OU blood_pressure_week29_diastolic >= 110).
- **Pressão arterial na repetição do terceiro trimestre: sistólica ≥160 ou diastólica ≥110 mmHg [E_PA_rep_GRAVE]**: 362/4913 avaliações. Sinaliza quando (repeated_third_trimester_blood_pressure_systolic >= 160 OU repeated_third_trimester_blood_pressure_diastolic >= 110).
- **Extremos da idade reprodutiva: idade <18 ou >40 anos [M02]**: 354/5000 avaliações. Sinaliza maternal_age < 18 ou > 40 (limites incluídos no intervalo).
- **Percentil de peso fetal estimado abaixo de 3 [E_EFW_P3]**: 260/4823 avaliações. Sinaliza estimated_fetal_weight_third_trimester_percentile < 3.
- **PTGO 1h: valor atinge limiar de hiperglicemia [E_PTGO_1h]**: 214/3241 avaliações. Quando OGTT_performed == 1: sinaliza OGTT_1h_glucose >= 180.
- **PTGO 2h: valor atinge limiar de hiperglicemia [E_PTGO_2h]**: 213/3241 avaliações. Quando OGTT_performed == 1: sinaliza OGTT_2h_glucose >= 153.
- **Peso ao nascer na categoria ≥4500 g [E_PESO_4500]**: 184/5000 avaliações. Sinaliza birth_weight_g >= 4500.
- **PAPP-A no limiar de marcador baixo [E_PAPPA]**: 175/4768 avaliações. Sinaliza PAPP_A <= 0.415.
- **Pressão arterial na semana 16: sistólica ≥140 ou diastólica ≥90 mmHg [E_PA_w16_ALTA]**: 114/5000 avaliações. Sinaliza quando (blood_pressure_week16_systolic >= 140 OU blood_pressure_week16_diastolic >= 90).
- **Pressão arterial na semana 6: sistólica ≥140 ou diastólica ≥90 mmHg [E_PA_w6_ALTA]**: 90/5000 avaliações. Sinaliza quando (blood_pressure_week6_systolic >= 140 OU blood_pressure_week6_diastolic >= 90).
- **Glicemia inicial em jejum atinge limiar de diabetes na gravidez [E_GLIC_DIABETES]**: 20/5000 avaliações. Sinaliza fasting_glucose >= 126.
- **PTGO 2 h atinge limiar de diabetes na gravidez [E_PTGO_DM2]**: 3/3241 avaliações. Quando OGTT_performed == 1: sinaliza OGTT_2h_glucose >= 200.
- **Pressão arterial no pós-parto: sistólica ≥160 ou diastólica ≥110 mmHg [E_PA_pos_GRAVE]**: 3/5000 avaliações. Sinaliza quando (postpartum_blood_pressure_systolic >= 160 OU postpartum_blood_pressure_diastolic >= 110).

## Notas sobre convenções de registo: 0 registos

Nenhuma ocorrência nas regras executadas deste grupo.


Consultar relatorio.html para critérios completos, regras desativadas, notas e referências.

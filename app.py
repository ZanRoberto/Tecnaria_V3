                    (document_question[:3000], previous_question[:800] if has_memory else "")
                    in PLAN_CACHE
                )
                clean = not re.match(
                    r"(Si è verificato|Informazione non trovata|Il motore esterno|Archivio)",
                    document_answer,
                ) and "DATI DA VERIFICARE" not in document_answer
                if clean and plan_ok and use_cache:
                    ANSWER_CACHE[cache_key] = (document_answer, is_followup_turn)
                    if len(ANSWER_CACHE) > 256:
                        ANSWER_CACHE.popitem(last=False)

            return AnswerResponse(
                answer=document_answer,
                source="narratore_risponditore",
                meta={
                    "mode": document_mode,
                    "engine": active_document_engine(),
                    "used_previous_context": bool(is_followup_turn),
                    "cached": cached is not None,
                },
            )

        # 1) DOMANDE AZIENDALI / COMMERCIALI → SOLO COMM.JSON
        if is_commercial_question(q_norm):
            comm_block = match_comm(q_norm)
            if comm_block:
                answer = comm_block.get("response_variants", {}).get("gold", {}).get("it")
                if not answer:
                    answer = comm_block.get("answer_it") or comm_block.get("answer", "")
                return AnswerResponse(
                    answer=answer,
                    source="json_comm",
                    meta={"comm_id": comm_block.get("id")},
                )
            else:
                return AnswerResponse(
                    answer=(
                        "Le informazioni richieste rientrano nei dati aziendali/commerciali. "
                        "Per sicurezza è necessario fare riferimento ai canali ufficiali Tecnaria."
                    ),
                    source="json_comm_fallback",
                    meta={},
                )

        # 2) DESCRIZIONE SITUAZIONALE → NARRATORE + SUPERRISPONDITORE
        if is_situational(question_raw):
            # Step 1: Narratore legge la situazione
            analisi_narratore = call_deepseek(
                SYSTEM_PROMPT_NARRATORE,
                question_raw,
                temperature=0.2
            )

            # Step 2: Superrisponditore risponde con contesto completo
            contesto_super = (
                f"DESCRIZIONE CLIENTE:\n{question_raw}\n\n"
                f"ANALISI NARRATORE:\n{analisi_narratore}\n\n"
                f"Ora dai la risposta tecnica completa."
            )
            risposta_super = call_deepseek(
                SYSTEM_PROMPT_SUPERRISPONDITORE,
                contesto_super,
                temperature=0.2
            )

            risposta_finale = (
                f"📋 ANALISI SITUAZIONE\n\n{analisi_narratore}"
                f"\n\n{'─' * 40}\n\n"
                f"💡 RISPOSTA TECNICA\n\n{risposta_super}"
            )

            return AnswerResponse(
                answer=risposta_finale,
                source="oracolo_narratore_superrisponditore",
                meta={
                    "narratore": analisi_narratore,
                    "superrisponditore": risposta_super,
                    "used_deepseek": True,
                },
            )

        # 3) DOMANDE TECNICHE DIRETTE → DEEPSEEK GOLD TECNARIA
        gpt_answer = call_deepseek(SYSTEM_PROMPT_GOLD, question_raw, temperature=0.2)
        kb_block = match_from_kb(question_raw)
        kb_id = kb_block.get("id") if kb_block else None

        return AnswerResponse(
            answer=gpt_answer,
            source="deepseek_gold_tecnaria",
            meta={
                "used_deepseek": True,
                "kb_id": kb_id,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] /api/ask: {e}")
        return AnswerResponse(
            answer="Si è verificato un problema interno. Contatta l’Ufficio Tecnico Tecnaria.",
            source="error",
            meta={"exception": str(e)},
        )

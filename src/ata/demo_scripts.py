"""Roteiros originais do `ata demo` (reuniões inventadas) em pt-BR, en e es, com gabarito.

Cada roteiro: 3 pessoas — "Eu" no mic e "A"/"B" no far —, um produto fictício, fatos verificáveis (números),
uma decisão, duas ações com responsável e prazo, uma pergunta em aberto e uma fala sobreposta
(``overlap=True``). O gabarito vira ``demo-reference.json`` no bundle.
"""

from __future__ import annotations

from typing import Any

from .testing import Line

L = Line

SCRIPTS: dict[str, dict[str, Any]] = {
    "pt-BR": {
        "title": "Revisão do Maré app de hortas",
        "people": {"Eu": "Você", "A": "Lívia", "B": "Otávio"},
        "lines": [
            L("Eu", "Boa tarde pessoal obrigado por entrarem na revisão do Maré"),
            L("Eu", "Hoje quero fechar a data da versão para as hortas comunitárias"),
            L("A", "Trouxe os números do piloto em Niterói"),
            L("A", "Foram trinta e oito hortas cadastradas em seis semanas"),
            L("A", "E mil e duzentas regas registradas pelo aplicativo"),
            L("Eu", "Quantas hortas continuaram usando depois do primeiro mês"),
            L("A", "Vinte e nove das trinta e oito seguem ativas"),
            L("B", "Do lado técnico o sensor de umidade ainda falha com chuva forte"),
            L("B", "Em dias de tempestade perdemos uns quinze por cento das leituras"),
            L("Eu", "Isso afeta o alerta de rega"),
            L("B", "Afeta sim o alerta dispara atrasado"),
            L("A", "Os voluntários reclamaram disso no grupo", overlap=True),
            L("Eu", "Então qual é o caminho mais seguro"),
            L("B", "Posso trocar a leitura direta por uma média de dez minutos"),
            L("A", "Eu prefiro lançar com a média e melhorar depois"),
            L("Eu", "Concordo ficou definido que lançamos a versão dois no dia quatorze de novembro"),
            L("B", "Fechado eu ajusto a média do sensor até sexta feira"),
            L("A", "Eu vou preparar o guia para os voluntários até o dia dez"),
            L("Eu", "Ótimo e o custo do servidor"),
            L("B", "Hoje gastamos trezentos e quarenta reais por mês"),
            L("B", "Com o dobro de hortas deve chegar perto de seiscentos"),
            L("Eu", "Cabe no orçamento por enquanto"),
            L("A", "Só falta saber uma coisa"),
            L("A", "A prefeitura vai liberar o uso das praças para novas hortas?"),
            L("Eu", "Ninguém sabe ainda fica como pergunta em aberto obrigado a todos"),
        ],
        "reference": {
            "product": "Maré",
            "facts": ["38 hortas cadastradas em 6 semanas", "1200 regas registradas", "29 de 38 hortas ativas",
                      "15% das leituras perdidas em tempestade", "custo do servidor R$ 340 por mês",
                      "estimativa perto de R$ 600 com o dobro de hortas"],
            "decisions": ["lançar a versão dois em 14 de novembro, com a média de dez minutos do sensor"],
            "actions": [{"text": "ajustar a média do sensor", "owner": "Otávio", "due": "sexta-feira"},
                        {"text": "preparar o guia para os voluntários", "owner": "Lívia", "due": "dia 10"}],
            "questions": ["a prefeitura vai liberar o uso das praças para novas hortas?"],
        },
    },
    "en": {
        "title": "Lumen bike lights launch sync",
        "people": {"Eu": "You", "A": "Priya", "B": "Marcus"},
        "lines": [
            L("Eu", "Thanks for joining the Lumen launch sync"),
            L("Eu", "The goal today is to lock the shipping date for the rear light"),
            L("A", "I have the numbers from the beta group"),
            L("A", "Two hundred and ten riders tested it for five weeks"),
            L("A", "Average battery life was nineteen hours per charge"),
            L("Eu", "How many riders reported problems"),
            L("A", "Seventeen of them mostly about the mounting clip"),
            L("B", "The clip cracks below minus five degrees"),
            L("B", "Our supplier can switch to a nylon blend for forty cents more per unit"),
            L("Eu", "Does that change the retail price"),
            L("B", "Not if we keep the margin at thirty one percent"),
            L("A", "Retailers already asked about the cold weather issue", overlap=True),
            L("Eu", "So the nylon clip is the safer option"),
            L("B", "Yes and the tooling takes about three weeks"),
            L("A", "That still fits a December window"),
            L("Eu", "Alright we decided to ship on December second with the nylon clip"),
            L("B", "I will place the tooling order with the supplier by Thursday"),
            L("A", "I will update the retailer brochure by next Monday"),
            L("Eu", "Good and what about the warehouse"),
            L("B", "We have room for eight thousand units right now"),
            L("A", "First orders add up to about five thousand"),
            L("Eu", "Then we are covered for launch"),
            L("A", "One thing nobody has answered yet"),
            L("A", "Do we offer the clip replacement for free to the beta riders?"),
            L("Eu", "Let us leave that open for now thanks everyone"),
        ],
        "reference": {
            "product": "Lumen",
            "facts": ["210 riders tested for 5 weeks", "19 hours average battery life", "17 riders reported problems",
                      "clip cracks below -5 degrees", "nylon blend costs 40 cents more per unit", "31% margin",
                      "warehouse room for 8000 units", "first orders about 5000"],
            "decisions": ["ship on December 2 with the nylon clip"],
            "actions": [{"text": "place the tooling order with the supplier", "owner": "Marcus", "due": "Thursday"},
                        {"text": "update the retailer brochure", "owner": "Priya", "due": "next Monday"}],
            "questions": ["do we offer the clip replacement for free to the beta riders?"],
        },
    },
    "es": {
        "title": "Revisión de Brisa reservas de laboratorio",
        "people": {"Eu": "Tú", "A": "Valeria", "B": "Joaquín"},
        "lines": [
            L("Eu", "Buenos días gracias por venir a la revisión de Brisa"),
            L("Eu", "Hoy necesitamos cerrar la fecha para las universidades"),
            L("A", "Traigo los datos de la prueba en Valparaíso"),
            L("A", "Participaron cuatro facultades y ciento sesenta docentes"),
            L("A", "Se hicieron novecientas reservas de laboratorio en un mes"),
            L("Eu", "Cuántas reservas se cancelaron"),
            L("A", "Ochenta y dos casi todas por choques de horario"),
            L("B", "El calendario no sincroniza bien con el sistema de la facultad"),
            L("B", "Tarda hasta veinte minutos en reflejar un cambio"),
            L("Eu", "Eso explica los choques"),
            L("B", "Sí con un webhook bajaría a menos de un minuto"),
            L("A", "Los docentes pidieron justo eso en la encuesta", overlap=True),
            L("Eu", "Entonces conviene esperar al webhook"),
            L("B", "Puedo tenerlo listo en dos semanas"),
            L("A", "Así llegamos antes del segundo semestre"),
            L("Eu", "Perfecto quedamos en lanzar el tres de marzo con el webhook"),
            L("B", "Yo termino el webhook antes del viernes veinte"),
            L("A", "Yo voy a escribir el manual para los docentes antes del lunes"),
            L("Eu", "Bien y el costo del piloto"),
            L("B", "Fueron dos mil cuatrocientos dólares en total"),
            L("B", "La mitad fue el servidor de pruebas"),
            L("Eu", "Está dentro de lo previsto"),
            L("A", "Me queda una duda"),
            L("A", "Las facultades privadas van a pagar la misma licencia?"),
            L("Eu", "Lo dejamos abierto por ahora gracias a todos"),
        ],
        "reference": {
            "product": "Brisa",
            "facts": ["4 facultades y 160 docentes", "900 reservas en un mes", "82 reservas canceladas",
                      "hasta 20 minutos para sincronizar", "webhook en 2 semanas", "piloto costó 2400 dólares"],
            "decisions": ["lanzar el 3 de marzo con el webhook"],
            "actions": [{"text": "terminar el webhook", "owner": "Joaquín", "due": "viernes 20"},
                        {"text": "escribir el manual para los docentes", "owner": "Valeria", "due": "lunes"}],
            "questions": ["¿las facultades privadas van a pagar la misma licencia?"],
        },
    },
}


def script(language: str) -> dict[str, Any]:
    return SCRIPTS[language]


def reference(language: str) -> dict[str, Any]:
    s = SCRIPTS[language]
    overlap = [i for i, ln in enumerate(s["lines"]) if ln.overlap]
    return {"schema": "ata-demo/1", "language": language, "title": s["title"], "people": s["people"],
            "lines": len(s["lines"]), "overlap_lines": overlap, **s["reference"]}

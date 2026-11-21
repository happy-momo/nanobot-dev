"""EdgeAdviser — edge-deployed equipment intelligence agent built on nanobot.

Layout:
    edge_adviser/
        config/    domain models + configuration loading
        core/      asset registry + knowledge store
        analytics/ deterministic time-series fact engine
        ingest/    data source adapters (simulator/REST/MQTT/OPC-UA/Modbus)
        llm/       nanobot SDK gateway + context injection + degradation
        report/    report generation & archiving
        learn/     historical learning loop
        qa/        question-answer engine
"""

__version__ = "0.4.3"
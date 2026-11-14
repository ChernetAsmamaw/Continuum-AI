"""EHR ingestion layer.

Hospital systems → Continuum, without anyone copy-pasting:

* ``hl7``      parse HL7v2 ADT messages (the discharge "doorbell", ADT^A03)
* ``mllp``     optional MLLP listener for interface engines
* ``fhir_client`` FHIR R4 REST client (no auth, bearer, or SMART backend-services JWT)
* ``pull``     fetch a discharge from the EHR into a normalized ``DischargePacket``
* ``builder``  turn a packet into a patient, episode and follow-ups
               (structured orders first, AI extraction of the narrative second)
* ``poller``   re-query the EHR for results behind open follow-ups
* ``service``  orchestration used by the API routes and the scheduler
* ``scenarios`` synthetic discharge scenarios layered on Synthea patients
"""

# AI Visa Sourcing Chatbot — Proof of Concept (POC)

## 1. Objective

Build an AI-powered chatbot that answers visa-related questions using the provided dataset. The chatbot must retrieve information from the dataset and return structured responses through an API endpoint. Consider the Vendor endpoint as an endpoint that can be used in the chatbot application. Vendor is the POC developer. The solution will be evaluated using an automated test harness.

**POC Harness:** <https://visa-app-harness-jodgmznemkprpy72m5v479.streamlit.app/>

The harness contains:

- Dataset collections
- API contract
- Public and hidden evaluation tests
- Response format expectations
- Way to test the chatbot usage

## 2. POC Scope

The chatbot should answer questions related to:

- Visa eligibility
- Visa type recommendations
- Required documents
- Visa duration and processing time
- Travel recommendations based on interests

Responses must be grounded strictly in the dataset provided in the harness. The bot must not generate information that is not supported by the dataset.

## 3. Integration

Vendors must expose a single API endpoint that the harness can call.

The request structure and response format are available in the **Contract** section of the harness. During testing, the harness will send:

- User question
- User context (such as nationality and residency)

The chatbot must return a structured response containing the answer and supporting dataset references.

## 4. Chatbot Behaviour

The chatbot should:

- Retrieve information from the dataset collections
- Return correct visa eligibility and document requirements
- Handle unsupported queries with a refusal response
- Provide grounded responses without hallucinations

During automated testing, the user context will already be provided, so the chatbot should respond directly without asking additional questions.

## 5. Evaluation

The solution will be evaluated through automated tests in the harness.

Evaluation criteria include:

- Response accuracy
- Correct eligibility and document rules
- Grounding in dataset information
- Handling of unsupported queries
- Response latency

Public tests are visible to vendors. Hidden tests will be used for final evaluation.

## 6. Architecture Overview

Vendors should briefly explain their approach, including:

- How the chatbot retrieves information from the dataset collections
- How eligibility rules are applied when generating answers
- How hallucinations outside the dataset are prevented
- Expected latency and scalability approach

A short description or architecture diagram is sufficient.

## 7. Expected Deliverables

Vendors should provide:

- Working API endpoint
- Brief architecture overview of the AI approach
- Explanation of how dataset retrieval is performed
- Expected response latency and scaling considerations

The endpoint should be accessible so it can be tested through the harness.

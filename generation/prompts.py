"""All prompt templates ported from WrenAI, stored as Jinja2 strings."""

# ── SQL Generation ──────────────────────────────────────────────────────────────

TEXT_TO_SQL_RULES = """
### SQL RULES ###
- ONLY USE SELECT statements, NO DELETE, UPDATE OR INSERT etc. statements that might change the data in the database.
- ONLY USE the tables and columns mentioned in the database schema.
- ONLY USE "*" if the user query asks for all the columns of a table.
- ONLY CHOOSE columns belong to the tables mentioned in the database schema.
- DON'T INCLUDE comments in the generated SQL query.
- YOU MUST USE "JOIN" if you choose columns from multiple tables!
- PREFER USING CTEs over subqueries.
- When generating SQL query, always:
    - Put double quotes around column and table names.
    - Put single quotes around string literals.
    - Never quote numeric literals.
    For example: SELECT "customers"."customer_name" FROM "customers" WHERE "customers"."city" = 'Taipei' and "customers"."year" = 1992;
- YOU MUST USE "lower(<table_name>.<column_name>) like lower(<value>)" function or "lower(<table_name>.<column_name>) = lower(<value>)" function for case-insensitive comparison!
    - Use "lower(<table_name>.<column_name>) LIKE lower(<value>)" when:
        - The user requests a pattern or partial match.
        - The value is not specific enough to be a single, exact value.
        - Wildcards (%) are needed to capture the pattern.
    - Use "lower(<table_name>.<column_name>) = lower(<value>)" when:
        - The user requests an exact, specific value.
        - There is no ambiguity or pattern in the value.
- USE THE VIEW TO SIMPLIFY THE QUERY.
- DON'T MISUSE THE VIEW NAME. THE ACTUAL NAME IS FOLLOWING THE CREATE VIEW STATEMENT.
- ONLY USE table/column alias in the final SELECT clause; don't use table/column alias in the other clauses.
- DON'T USE "FILTER(WHERE <expression>)" clause in the generated SQL query.
- DON'T USE "EXTRACT(EPOCH FROM <expression>)" clause in the generated SQL query.
- DON'T USE INTERVAL or generate INTERVAL-like expression in the generated SQL query.
- DON'T USE "TO_CHAR" function in the generated SQL query.
- Aggregate functions are not allowed in the WHERE clause. Instead, they belong in the HAVING clause, which is used to filter after aggregation.
- You can only add "ORDER BY" and "LIMIT" to the final "UNION" result.
- For the ranking problem, you must use the ranking function, `DENSE_RANK()` to rank the results and then use `WHERE` clause to filter the results.
- For the ranking problem, you must add the ranking column to the final SELECT clause.
"""

CALCULATED_FIELD_INSTRUCTIONS = """
#### Instructions for Calculated Field ####

The first structure is the special column marked as "Calculated Field". You need to interpret the purpose and calculation basis for these columns, then utilize them in the following text-to-sql generation tasks.
First, provide a brief explanation of what each field represents in the context of the schema, including how each field is computed using the relationships between models.
Then, during the following tasks, if the user queries pertain to any calculated fields defined in the database schema, ensure to utilize those calculated fields appropriately in the output SQL queries.
The goal is to accurately reflect the intent of the question in the SQL syntax, leveraging the pre-computed logic embedded within the calculated fields.
"""

METRIC_INSTRUCTIONS = """
#### Instructions for Metric ####

Second, you will learn how to effectively utilize the special "metric" structure in text-to-SQL generation tasks.
Metrics in a data model simplify complex data analysis by structuring data through predefined dimensions and measures.
This structuring closely mirrors the concept of OLAP (Online Analytical Processing) cubes but is implemented in a more flexible and SQL-friendly manner.

If the given schema contains the structures marked as 'metric', you should first interpret the metric schema.
Then, during the following tasks, if the user queries pertain to any metrics defined in the database schema, ensure to utilize those metrics appropriately in the output SQL queries.
The target is making complex data analysis more accessible and manageable by pre-aggregating data and structuring it using the metric structure, and supporting direct querying for business insights.
"""

SQL_GENERATION_SYSTEM_PROMPT = f"""
You are a helpful assistant that converts natural language queries into ANSI SQL queries.

Given user's question, database schema, etc., you should think deeply and carefully and generate the SQL query based on the given reasoning plan step by step.

### GENERAL RULES ###

1. YOU MUST FOLLOW the instructions strictly to generate the SQL query if the section of USER INSTRUCTIONS is available in user's input.
2. YOU MUST REFER to the sql samples and learn the usage of the schema structures and how SQL is written based on them if the section of SQL SAMPLES is available in user's input.
3. YOU MUST FOLLOW SQL Rules if they are not contradicted with instructions.

{TEXT_TO_SQL_RULES}

### FINAL ANSWER FORMAT ###
The final answer must be a ANSI SQL query in JSON format:

{{
    "sql": <SQL_QUERY_STRING>
}}
"""

SQL_GENERATION_USER_TEMPLATE = """
### DATABASE SCHEMA ###
{% for document in documents %}
    {{ document }}
{% endfor %}

{% if calculated_field_instructions %}
{{ calculated_field_instructions }}
{% endif %}

{% if metric_instructions %}
{{ metric_instructions }}
{% endif %}

{% if sql_samples %}
### SQL SAMPLES ###
{% for sample in sql_samples %}
Question:
{{sample.question}}
SQL:
{{sample.sql}}
{% endfor %}
{% endif %}

{% if instructions %}
### USER INSTRUCTIONS ###
{% for instruction in instructions %}
{{ loop.index }}. {{ instruction }}
{% endfor %}
{% endif %}

### QUESTION ###
User's Question: {{ query }}

Let's think step by step.
"""

# ── SQL Correction ──────────────────────────────────────────────────────────────

SQL_CORRECTION_SYSTEM_PROMPT = f"""
### TASK ###
You are an ANSI SQL expert with exceptional logical thinking skills and debugging skills, you need to fix the syntactically incorrect ANSI SQL query.

### SQL CORRECTION INSTRUCTIONS ###

1. First, think hard about the error message, and figure out the root cause first(please use the DATABASE SCHEMA and USER INSTRUCTIONS to help you figure out the root cause).
2. Then, generate the syntactically correct ANSI SQL query to correct the error.

### SQL RULES ###
Make sure you follow the SQL Rules strictly.

{TEXT_TO_SQL_RULES}

### FINAL ANSWER FORMAT ###
The final answer must be in JSON format:

{{
    "sql": <CORRECTED_SQL_QUERY_STRING>
}}
"""

SQL_CORRECTION_USER_TEMPLATE = """
{% if documents %}
### DATABASE SCHEMA ###
{% for document in documents %}
    {{ document }}
{% endfor %}
{% endif %}

{% if instructions %}
### USER INSTRUCTIONS ###
{% for instruction in instructions %}
{{ loop.index }}. {{ instruction }}
{% endfor %}
{% endif %}

### QUESTION ###
SQL: {{ invalid_generation_result.sql }}
Error Message: {{ invalid_generation_result.error }}

Let's think step by step.
"""

# ── Intent Classification ───────────────────────────────────────────────────────

INTENT_CLASSIFICATION_SYSTEM_PROMPT = """
### Task ###
You are an expert detective specializing in intent classification. Combine the user's current question and previous questions to determine their true intent based on the provided database schema. Classify the intent into one of these categories: `MISLEADING_QUERY`, `TEXT_TO_SQL`, or `GENERAL`. Additionally, provide a concise reasoning (maximum 20 words) for your classification.

### Instructions ###
- **Follow the user's previous questions:** If there are previous questions, try to understand the user's current question as following the previous questions.
- **Rephrase Question:** Rewrite follow-up questions into full standalone questions using prior conversation context.
- **Concise Reasoning:** The reasoning must be clear, concise, and limited to 20 words.
- **Vague Queries:** If the question is vague or does not related to a table or property from the schema, classify it as `MISLEADING_QUERY`.
- **Incomplete Queries:** If the question is related to the database schema but references unspecified values (e.g., "the following", "these", "those") without providing them, classify as `GENERAL`.

### Intent Definitions ###

<TEXT_TO_SQL>
**When to Use:**
- The user's inputs are about modifying SQL from previous questions.
- The user's inputs are related to the database schema and requires an SQL query.
- The question includes references to specific tables, columns, or data details.
- The question provides all necessary parameters to generate executable SQL.
</TEXT_TO_SQL>

<GENERAL>
**When to Use:**
- The user seeks general information about the database schema or its overall capabilities.
- The query references missing information (e.g., "the following items" without listing them).
- The query is incomplete for SQL generation despite mentioning database concepts.
</GENERAL>

<MISLEADING_QUERY>
**When to Use:**
- The user's inputs is irrelevant to the database schema or includes SQL code.
- The user's inputs lacks specific details needed to generate an SQL query.
- It appears off-topic or is simply a casual conversation starter.
</MISLEADING_QUERY>

### Output Format ###
Return your response as a JSON object with the following structure:

{
    "rephrased_question": "<rephrased question in full standalone question if there are previous questions, otherwise the original question>",
    "reasoning": "<brief chain-of-thought reasoning (max 20 words)>",
    "results": "MISLEADING_QUERY" | "TEXT_TO_SQL" | "GENERAL"
}
"""

INTENT_CLASSIFICATION_USER_TEMPLATE = """
### DATABASE SCHEMA ###
{% for db_schema in db_schemas %}
    {{ db_schema }}
{% endfor %}

{% if sql_samples %}
### SQL SAMPLES ###
{% for sql_sample in sql_samples %}
Question:
{{sql_sample.question}}
SQL:
{{sql_sample.sql}}
{% endfor %}
{% endif %}

{% if instructions %}
### USER INSTRUCTIONS ###
{% for instruction in instructions %}
{{ loop.index }}. {{ instruction }}
{% endfor %}
{% endif %}

### INPUT ###
{% if histories %}
User's previous questions:
{% for history in histories %}
Question:
{{ history.question }}
SQL:
{{ history.sql }}
{% endfor %}
{% endif %}

User's current question: {{query}}

Let's think step by step
"""

# ── SQL Answer Generation ──────────────────────────────────────────────────────

SQL_ANSWER_SYSTEM_PROMPT = """### TASK
You are a data analyst that great at answering non-technical user's questions based on the data and sql so that even non-technical users can easily understand.
Please answer the user's question in concise and clear manner in Markdown format.

### INSTRUCTIONS
1. Read the user's question and understand the user's intention.
2. Read the sql and understand the data.
3. Make sure the answer is aimed for non-technical users, so don't mention any technical terms such as SQL syntax.
4. Generate a concise and clear answer to answer the user's question based on the data and sql.
5. If answer is in list format, only list top few examples, and tell users there are more results omitted.
6. Answer must be in the same language user specified.
7. Do not include ```markdown or ``` in the answer."""

SQL_ANSWER_USER_TEMPLATE = """### Inputs ###
User's question: {{ query }}
SQL: {{ sql }}
Data:
columns: {{ columns }}
rows: {{ rows }}
{% if truncation_note %}

Note: The data shown above is a sample of {{ num_rows_used }} rows out of {{ total_rows }} total rows returned by the query. Summarize based on the sample and mention that there are more results.
{% endif %}

Please think step by step and answer the user's question."""

CREATE FULLTEXT INDEX `column_text` FOR (n:`Column`) ON EACH [n.`search_text`] OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words',`fulltext.eventually_consistent`: false}};
CREATE CONSTRAINT `agent_id` FOR (n:`Agent`) REQUIRE (n.`id`) IS UNIQUE;
CREATE CONSTRAINT `claim_id` FOR (n:`Claim`) REQUIRE (n.`id`) IS UNIQUE;
CREATE CONSTRAINT `column_id` FOR (n:`Column`) REQUIRE (n.`id`) IS UNIQUE;
CREATE CONSTRAINT `exposure_id` FOR (n:`Exposure`) REQUIRE (n.`id`) IS UNIQUE;
CREATE CONSTRAINT `owner_id` FOR (n:`Owner`) REQUIRE (n.`id`) IS UNIQUE;
CREATE CONSTRAINT `project_id` FOR (n:`Project`) REQUIRE (n.`id`) IS UNIQUE;
CREATE CONSTRAINT `relation_id` FOR (n:`Relation`) REQUIRE (n.`id`) IS UNIQUE;

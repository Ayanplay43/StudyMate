-- StudyMate Endterm: exact reproducible queries based on synthetic SQLite records

-- Q1
SELECT s.name AS subject, COUNT(*) AS posts
FROM posts p JOIN subjects s ON s.id = p.subject_id
GROUP BY s.id, s.name ORDER BY posts DESC, s.name ASC;

-- Q2
SELECT category, COUNT(*) AS posts
FROM posts GROUP BY category ORDER BY posts DESC, category ASC;

-- Q3
SELECT status, COUNT(*) AS applications
FROM applications GROUP BY status ORDER BY applications DESC, status ASC;

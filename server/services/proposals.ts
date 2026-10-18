import { Router } from 'express';
import { getDatabase } from '../database.js';
const router = Router();
router.get('/proposals', async (req, res) => {
    const version = req.query.version || 'v1';
    const mode = req.query.mode || 'raw';
    if (!['v1', 'v2'].includes(version as string) || !['raw', 'chat'].includes(mode as string))
        return res.status(400).json({ error: 'Invalid version or mode.' });
    try {
        const db = await getDatabase();
        const rows = await db.all(
            `SELECT id,model_id as modelId,submitter_email as submitter,reason,votes,status,created_at as createdAt,mode,version FROM proposals WHERE version = ? AND mode = ? ORDER BY votes DESC,created_at DESC`,
            [version, mode],
        );
        res.json(rows);
    } catch (error) {
        console.error('Error fetching proposals:', error);
        res.status(500).json({ error: 'Internal server error' });
    }
});
export default router;

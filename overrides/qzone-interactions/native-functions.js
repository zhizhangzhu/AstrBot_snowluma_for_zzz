// Injected into the pinned SnowLuma bundle. Each account supplies its own cookies.
async function readQzoneLikes(cookieObject, targetUin, tid, beginUin = 0, num = 100) {
    const query = new URLSearchParams({g_tk: String(getBknFromCookie(cookieObject)), uin: String(targetUin),
        unikey: `http://user.qzone.qq.com/${targetUin}/mood/${tid}`, begin_uin: String(beginUin),
        query_count: String(num), if_first_page: beginUin ? '0' : '1', format: 'json'});
    const payload = parseQzoneJson(await qzoneReadText(
        `https://user.qzone.qq.com/proxy/domain/users.qzone.qq.com/cgi-bin/likes/get_like_list_app?${query}`,
        {Cookie: cookieToString(cookieObject), Referer: `https://user.qzone.qq.com/${targetUin}`}));
    if (payload.code !== 0 || (payload.subcode ?? 0) !== 0) throw new Error('qzone like list rejected');
    const data = payload.data;
    if (!data || !Array.isArray(data.like_uin_info) || !Number.isInteger(data.total_number) || data.total_number < 0)
        throw new Error('invalid qzone like list');
    const users = data.like_uin_info.map(user => {
        if (!/^[1-9]\d{4,14}$/.test(String(user.fuin))) throw new Error('invalid liker account');
        return {uin: String(user.fuin), name: String(user.nick ?? '').slice(0, 80)};
    });
    const last = users.length ? Number(users[users.length - 1].uin) : 0;
    return {tid, target_uin: String(targetUin), total: data.total_number, users,
        next_uin: users.length && (beginUin || users.length < data.total_number) ? last : 0};
}

async function replyQzoneComment(cookieObject, selfUin, hostUin, tid, commentId, commentUin, content, replyUin, replyName) {
    if (!/^\d+$/.test(String(commentId)) || !/^[1-9]\d{4,14}$/.test(String(commentUin)))
        throw new Error('invalid qzone reply parent');
    if (replyUin !== undefined || replyName !== undefined) {
        if (!/^[1-9]\d{4,14}$/.test(String(replyUin)) || !String(replyName ?? '').trim())
            throw new Error('invalid addressed reply author');
        const name = String(replyName).trim().replace(/%/g,'%25').replace(/,/g,'%2C').replace(/}/g,'%7D');
        content = `@{uin:${replyUin},nick:${name},auto:1} ${content}`;
    }
    const body = new URLSearchParams({uin: String(selfUin), hostUin: String(hostUin), hostuin: String(selfUin),
        topicId: `${hostUin}_${tid}`, commentId: String(commentId), commentUin: String(commentUin), content,
        inCharset: 'utf-8', outCharset: 'utf-8', ref: '', code_version: '1', format: 'fs',
        qzreferrer: `https://user.qzone.qq.com/${hostUin}`});
    // Qzone's ReplyViewModel posts to addreply_ugc, separately from root comments.
    // One write attempt. A transport timeout must not create a duplicate reply.
    const result = parseQzoneJson(await RequestUtil.HttpGetText(
        `https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_addreply_ugc?g_tk=${getBknFromCookie(cookieObject)}`,
        'POST', body.toString(), {Cookie: cookieToString(cookieObject), 'Content-Type': 'application/x-www-form-urlencoded'}));
    if (result.code !== 0 || (result.subcode ?? 0) !== 0) throw new Error('qzone comment reply rejected');
    const id = result.data?.id;
    if (!(typeof id === 'string' && id.trim() || Number.isSafeInteger(id) && id >= 0))
        throw new Error('qzone reply accepted without an ID; delivery uncertain');
    return {comment_id: String(id), parent_comment_id: String(commentId), parent_comment_uin: Number(commentUin)};
}

async function readQzoneNotifications(cookieObject, selfUin, offset = 0, count = 20, beginTime = '0', endTime = '0') {
    const query = new URLSearchParams({uin: String(selfUin), scope:'1', getappnotification:'1', getnotifi:'1',
        has_get_key:'0', set:'0', outputhtmlfeed:'1', useutf8:'1', offset:String(offset), count:String(count),
        begin_time:String(beginTime), end_time:String(endTime), g_tk:String(getBknFromCookie(cookieObject))});
    const response = parseQzoneCallback(await qzoneReadText(
        `https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds2_html_pav_all?${query}`,
        {Cookie:cookieToString(cookieObject),Referer:`https://user.qzone.qq.com/${selfUin}`}));
    if (!response || response.code !== 0 || (response.subcode ?? 0) !== 0)
        throw new Error('qzone related notifications rejected');
    const main=response.data?.main;
    const rows=response.data?.data;
    if (!main || !Array.isArray(rows) || ![true,false,0,1,'0','1'].includes(main.hasMoreFeeds))
        throw new Error('invalid qzone notification page');
    const hasMore=[true,1,'1'].includes(main.hasMoreFeeds);
    const pageOffset=Number(main.offset);
    const pageCount=Number(main.count);
    if (!Number.isSafeInteger(pageOffset) || pageOffset<0 || !Number.isSafeInteger(pageCount) || pageCount<0)
        throw new Error('invalid notification cursor');
    const nextOffset=pageOffset>offset ? pageOffset : offset+pageCount;
    if (hasMore && nextOffset<=offset) throw new Error('notification cursor did not advance');
    const items=rows.filter(row=>row && typeof row==='object' && !Array.isArray(row)).map(row=>{
        const item={};
        for(const key of ['appid','typeid','key','uin','opuin','ouin','oprType','abstime','title','summary','html'])
            item[key]=String(row[key] ?? '');
        return item;
    });
    return {self_uin:String(selfUin),items,has_more:hasMore,next_offset:nextOffset,
        begin_time:String(main.begin_time || beginTime),end_time:String(main.end_time || endTime)};
}

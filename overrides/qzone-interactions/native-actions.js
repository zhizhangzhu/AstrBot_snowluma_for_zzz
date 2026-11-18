    defineAction({
        name: 'get_qzone_likes', readOnly: true,
        summary: '读取说说点赞者（只读，受空间权限限制）',
        params: {tid: f$1.string({allowEmpty:false}), target_uin:f$1.userId().optional(),
            begin_uin:f$1.int({min:0}).default(0), num:f$1.int({min:1,max:100}).default(100)},
        run: async (p,ctx) => okResponse(await readQzoneLikes(await ctx.bridge.apis.web.getCookies('qzone.qq.com'),
            p.target_uin ?? ctx.bridge.identity.uin,p.tid,p.begin_uin,p.num))
    }),
    defineAction({
        name:'reply_qzone_comment', summary:'回复说说的指定评论楼层',
        params:{tid:f$1.string({allowEmpty:false}),target_uin:f$1.userId(),
            comment_id:f$1.string({allowEmpty:false}),comment_uin:f$1.userId(),content:f$1.string({allowEmpty:false}),
            reply_uin:f$1.userId().optional(),reply_name:f$1.string({allowEmpty:false}).optional()},
        run:async(p,ctx)=>okResponse(await replyQzoneComment(await ctx.bridge.apis.web.getCookies('qzone.qq.com'),
            ctx.bridge.identity.uin,p.target_uin,p.tid,p.comment_id,p.comment_uin,p.content,p.reply_uin,p.reply_name))
    }),
    defineAction({
        name:'get_qzone_notifications',readOnly:true,summary:'读取当前账号与我相关通知（不标记已读）',
        params:{offset:f$1.int({min:0,max:1000000}).default(0),count:f$1.int({min:1,max:50}).default(20),
            begin_time:f$1.string().default('0'),end_time:f$1.string().default('0')},
        run:async(p,ctx)=>okResponse(await readQzoneNotifications(await ctx.bridge.apis.web.getCookies('qzone.qq.com'),
            ctx.bridge.identity.uin,p.offset,p.count,p.begin_time,p.end_time))
    }),

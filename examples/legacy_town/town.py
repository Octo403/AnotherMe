from .models import Resident

RESIDENTS = {
    "lin": Resident(id="lin", name="林沐", persona="小镇图书管理员，重视事实与知识分享，习惯先查资料。"),
    "zhou": Resident(id="zhou", name="周禾", persona="小镇咖啡店店主，关注成本、邻里合作和实际可行性。"),
}


def simulate(agent, event: str, rounds: int = 2, session: str = "town-demo"):
    """Bounded turn-taking. Only public speech crosses resident memory boundaries."""
    public_log = []
    for tick in range(rounds):
        for resident in RESIDENTS.values():
            prompt = f"公共事件：{event}\n你会如何回应？请区分建议和已经发生的事实。"
            if public_log:
                prompt += "\n最近公开发言（仅供讨论）：\n" + "\n".join(public_log[-4:])
            reply = agent.ask(prompt, resident, session)
            public_log.append(f"{resident.name}：{reply.answer}")
            yield {"tick": tick + 1, **reply.model_dump()}

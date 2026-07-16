(() => {
  const C = window.SCPPER;
  const $ = (id) => document.getElementById(id);

  const COLORS = {
    attack: "#c62828",
    survive: "#2e7d32",
    utility: "#1565c0",
    hybrid: "#6a1b9a",
    ultimate: "#ff8f00",
  };

  const SKILL_META = {
    normal_attack: ["普通攻击", COLORS.attack],
    critical_hit: ["致命暴击", COLORS.attack],
    gain_shield: ["护盾", COLORS.survive],
    self_harm: ["反噬", COLORS.attack],
    heal: ["治疗", COLORS.survive],
    critical_heal: ["暴疗", COLORS.survive],
    two_more: ["再抽", COLORS.utility],
    blood_swap: ["换血", COLORS.utility],
    blood_swap1: ["换血追击", COLORS.attack],
    silence: ["沉默", COLORS.utility],
    undead_ultimate: ["终焉之剑", COLORS.ultimate],
    ice_attack: ["寒冰攻击", COLORS.attack],
    ice_silence: ["冰冻", COLORS.utility],
    bomb_attack: ["爆炸瓶", COLORS.attack],
    double_attack: ["燃血轰击", COLORS.attack],
    poison_attack: ["毒瓶", COLORS.attack],
    mage_ultimate: ["绝对零度", COLORS.ultimate],
    medicine_both_heal: ["群体治疗", COLORS.survive],
    medicine_crit_heal: ["嗜血之疗", COLORS.hybrid],
    medicine_crit_silence: ["沉默重击", COLORS.attack],
    medicine_boost_heal: ["治愈整备", COLORS.utility],
    medicine_mega_heal: ["愈合秘法", COLORS.ultimate],
    double_normal_attack: ["双重打击", COLORS.attack],
    attack_and_draw: ["快速袭击", COLORS.attack],
    attack_and_heal: ["嗜血一击", COLORS.hybrid],
    attack_and_shield: ["持盾袭击", COLORS.hybrid],
    half_hp_and_attack: ["裂空一击", COLORS.attack],
    double_next_attack: ["力量凝聚", COLORS.utility],
    self_harm_and_triple_critical: ["毁灭三连", COLORS.ultimate],
    double_deduction: ["无畏冲击", COLORS.attack],
    double_deduction_and_draw: ["无畏连打", COLORS.attack],
    half_hp_both: ["生命削减", COLORS.attack],
    attack_critical_draw: ["暴力连打", COLORS.attack],
    double_deduction_30_attack_critical_draw: ["终极连招", COLORS.ultimate],
    both_heal_10: ["双加10", COLORS.survive],
    critical_and_critical_heal_and_draw: ["未来之击", COLORS.ultimate],
    shield_and_self_harm_10: ["荆棘之盾", COLORS.survive],
    knight_ultimate: ["骑士奥义", COLORS.ultimate],
  };

  const CHARACTER_DEFS = [
    {
      id: "undead",
      name: "亡灵战神",
      desc: "掌控黑暗之力的不死战士",
      skills: [
        ["普通攻击", 30, "normal_attack"],
        ["致命暴击", 10, "critical_hit"],
        ["骸骨护盾", 15, "gain_shield"],
        ["黑暗反噬", 5, "self_harm"],
        ["生灵禁术", 15, "blood_swap1"],
        ["死亡尖啸", 5, "silence"],
        ["深渊治愈", 4, "heal"],
        ["燃血轰击", 15, "double_attack"],
        ["终焉之剑", 1, "undead_ultimate"],
      ],
    },
    {
      id: "frost",
      name: "冰霜法师",
      desc: "操纵寒冰之力的法术大师",
      skills: [
        ["普通攻击", 30, "normal_attack"],
        ["治疗之术", 10, "heal"],
        ["冰晶护盾", 15, "gain_shield"],
        ["寂灭冰封", 15, "ice_silence"],
        ["爆裂黎明", 5, "bomb_attack"],
        ["致命毒药", 4, "poison_attack"],
        ["冰蓝末日", 10, "ice_attack"],
        ["赤色复苏", 10, "heal"],
        ["绝对零度", 1, "mage_ultimate"],
      ],
    },
    {
      id: "loser",
      name: "流浪之人",
      desc: "搜寻垃圾之力的超级战士",
      skills: [
        ["捡起棍子", 55, "normal_attack"],
        ["捡起药瓶", 25, "heal"],
        ["高效捡拾", 20, "two_more"],
      ],
    },
    {
      id: "medicine",
      name: "药药超人",
      desc: "精通治疗之术的医疗专家",
      skills: [
        ["普通攻击", 30, "normal_attack"],
        ["治疗之术", 30, "heal"],
        ["群体治疗", 10, "medicine_both_heal"],
        ["嗜血之疗", 10, "medicine_crit_heal"],
        ["沉默重击", 10, "medicine_crit_silence"],
        ["治愈整备", 9, "medicine_boost_heal"],
        ["愈合秘法", 1, "medicine_mega_heal"],
      ],
    },
    {
      id: "legend",
      name: "传奇大剑",
      desc: "手持剑阁巨剑的战场主宰",
      skills: [
        ["普通攻击", 40, "normal_attack"],
        ["治疗之术", 20, "heal"],
        ["双重打击", 10, "double_normal_attack"],
        ["快速袭击", 5, "attack_and_draw"],
        ["嗜血一击", 5, "attack_and_heal"],
        ["持盾袭击", 5, "attack_and_shield"],
        ["裂空一击", 5, "half_hp_and_attack"],
        ["力量凝聚", 9, "double_next_attack"],
        ["毁灭三连", 1, "self_harm_and_triple_critical"],
      ],
    },
    {
      id: "car",
      name: "车王祥子",
      desc: "速度激情魔力的至高化身",
      skills: [
        ["普通攻击", 10, "normal_attack"],
        ["致命暴击", 30, "critical_hit"],
        ["治疗之术", 10, "heal"],
        ["狂暴治疗", 15, "critical_heal"],
        ["无畏冲击", 15, "double_deduction"],
        ["无畏连打", 10, "double_deduction_and_draw"],
        ["生命削减", 4, "half_hp_both"],
        ["暴力连打", 5, "attack_critical_draw"],
        ["终极连招", 1, "double_deduction_30_attack_critical_draw"],
      ],
    },
    {
      id: "tata",
      name: "塔塔塔塔",
      desc: "喜欢用火把的塔希斯小姐",
      skills: [
        ["普通攻击", 35, "normal_attack"],
        ["治疗之术", 15, "heal"],
        ["致命暴击", 10, "critical_hit"],
        ["狂暴治疗", 5, "critical_heal"],
        ["不凡护盾", 5, "gain_shield"],
        ["无畏冲击", 4, "double_deduction"],
        ["群体治疗", 4, "both_heal_10"],
        ["无中生有", 7, "two_more"],
        ["沉默猫咪", 5, "silence"],
        ["生命转换", 3, "blood_swap"],
        ["生命削减", 6, "half_hp_both"],
        ["未来之击", 1, "critical_and_critical_heal_and_draw"],
      ],
    },
    {
      id: "knight",
      name: "剑盾骑士",
      desc: "顶级攻守兼备的战场守护",
      skills: [
        ["普通攻击", 25, "normal_attack"],
        ["治疗之术", 5, "heal"],
        ["连续打击", 20, "attack_and_draw"],
        ["守护之盾", 25, "gain_shield"],
        ["荆棘之盾", 5, "shield_and_self_harm_10"],
        ["重压晕眩", 6, "silence"],
        ["生命削减", 13, "half_hp_both"],
        ["骑士奥义", 1, "knight_ultimate"],
      ],
    },
  ];

  const CHARACTER_BY_ID = Object.fromEntries(CHARACTER_DEFS.map((item) => [item.id, item]));

  function clampName(value, fallback) {
    const clean = String(value || "").trim().replace(/\s+/g, " ").slice(0, 18);
    return clean || fallback;
  }

  function createFighter(selection, fallbackName) {
    const def = CHARACTER_BY_ID[selection.characterId] || CHARACTER_DEFS[0];
    const playerName = clampName(selection.name, fallbackName);
    return {
      displayName: `${playerName}(${def.name})`,
      playerName,
      characterId: def.id,
      characterName: def.name,
      health: 100,
      shield: 0,
      actionPoints: 0,
      healMultiplier: 1,
      attackMultiplier: 1,
    };
  }

  class BattleEngine {
    constructor(selections) {
      this.players = [
        createFighter(selections.p1, "玩家1"),
        createFighter(selections.p2, "玩家2"),
      ];
      this.players[0].actionPoints = 1;
      this.players[1].actionPoints = 0;
      this.current = 0;
      this.phase = 1;
      this.gameOver = false;
      this.roundEnd = false;
      this.skillText = "等待行动";
      this.skillColor = "#667085";
      this.logs = [];
      this.log(`\n╔${"=".repeat(45)}`);
      this.log(`║ ${"战斗开始!".padStart(25).padEnd(44)} `);
      this.log(`║   ${this.players[0].displayName} VS ${this.players[1].displayName}`);
      this.log(`╚${"=".repeat(45)}`);
      this.log(`\n${this.players[0].displayName} 先手行动！`);
    }

    clone() {
      return {
        players: this.players.map((player) => ({ ...player })),
        current: this.current,
        phase: this.phase,
        gameOver: this.gameOver,
        roundEnd: this.roundEnd,
        skillText: this.skillText,
        skillColor: this.skillColor,
        logs: this.logs.slice(-240),
      };
    }

    static fromSnapshot(snapshot) {
      const engine = Object.create(BattleEngine.prototype);
      engine.players = snapshot.players.map((player) => ({ ...player }));
      engine.current = snapshot.current;
      engine.phase = snapshot.phase;
      engine.gameOver = snapshot.gameOver;
      engine.roundEnd = snapshot.roundEnd;
      engine.skillText = snapshot.skillText;
      engine.skillColor = snapshot.skillColor;
      engine.logs = snapshot.logs || [];
      return engine;
    }

    log(text, color = "") {
      this.logs.push({ text, color });
    }

    currentPlayer() {
      return this.current == null ? null : this.players[this.current];
    }

    targetPlayer() {
      return this.current == null ? null : this.players[this.current === 0 ? 1 : 0];
    }

    takeAction() {
      if (this.gameOver || this.current == null) return;
      const attacker = this.currentPlayer();
      const target = this.targetPlayer();
      const result = this.rollSkill(attacker, target);
      const meta = result.handler ? SKILL_META[result.handler] : null;
      this.skillColor = meta?.[1] || "#151b23";
      this.skillText = result.valid ? `${attacker.displayName} 使用了 [${result.name}]` : `${attacker.displayName} ${result.name}`;
      if (result.valid) attacker.actionPoints -= 1;
      if (this.checkGameState()) return;
      if (attacker.actionPoints <= 0) {
        if (this.current === 0) {
          this.players[1].actionPoints += 1;
          this.current = 1;
          this.log(`\n${this.players[1].displayName} 开始行动！`);
        } else {
          this.current = 0;
          this.log("\n回合结束!");
        }
      }
      if (this.players[1].actionPoints <= 0 && this.current === 0 && this.players[0].actionPoints <= 0) {
        this.endRound();
        return;
      }
      this.checkGameState();
    }

    rollSkill(attacker, target) {
      if (attacker.health <= 0) return { name: "无法行动（死亡）", valid: false };
      if (attacker.actionPoints <= 0) {
        this.log(`${attacker.displayName} 没有行动点，无法行动！`);
        return { name: "无法行动", valid: false };
      }
      const def = CHARACTER_BY_ID[attacker.characterId] || CHARACTER_DEFS[0];
      const roll = Math.floor(Math.random() * 100) + 1;
      let cumulative = 0;
      for (const [name, probability, handler] of def.skills) {
        cumulative += probability;
        if (roll <= cumulative) {
          HANDLERS[handler](attacker, target, this);
          return { name, handler, valid: true };
        }
      }
      this.log(`${attacker.displayName} 犹豫不决，没有行动！`);
      return { name: "犹豫不决", valid: true };
    }

    endRound() {
      this.log(`\n===== 第 ${this.phase} 回合结束 =====`);
      this.roundEnd = true;
      this.current = null;
    }

    nextRound() {
      if (this.gameOver || !this.roundEnd) return;
      this.phase += 1;
      this.current = 0;
      this.roundEnd = false;
      this.players[0].actionPoints += 1;
      this.log(`\n===== 第 ${this.phase} 回合开始 =====`);
      this.log(`${this.players[0].displayName} 开始行动！`);
      this.skillText = "新回合开始";
      this.skillColor = "#1565c0";
    }

    checkGameState() {
      const [p1, p2] = this.players;
      if (p1.health <= 0 && p2.health <= 0) {
        this.log("\n╔══════════════════════════════════════════════════");
        this.log("║                  平局！双方同归于尽                ");
        this.log("╚══════════════════════════════════════════════════");
        this.skillText = "平局！双方同归于尽";
        this.skillColor = COLORS.ultimate;
        this.gameOver = true;
        return true;
      }
      if (p1.health <= 0) {
        this.log("\n╔══════════════════════════════════════════════════");
        this.log(`║ ${p2.displayName} 获胜！`);
        this.log("╚══════════════════════════════════════════════════");
        this.skillText = `${p2.displayName} 获胜！`;
        this.skillColor = COLORS.ultimate;
        this.gameOver = true;
        return true;
      }
      if (p2.health <= 0) {
        this.log("\n╔══════════════════════════════════════════════════");
        this.log(`║ ${p1.displayName} 获胜！`);
        this.log("╚══════════════════════════════════════════════════");
        this.skillText = `${p1.displayName} 获胜！`;
        this.skillColor = COLORS.ultimate;
        this.gameOver = true;
        return true;
      }
      return false;
    }
  }

  const HANDLERS = {
    normal_attack(attacker, target, engine) {
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${attacker.displayName}的攻击被${target.displayName}格挡！`);
      } else {
        const damage = 10 * attacker.attackMultiplier;
        target.health -= damage;
        engine.log(`${attacker.displayName}攻击！${target.displayName}受到${damage}点伤害`);
      }
      attacker.attackMultiplier = 1;
    },
    critical_hit(attacker, target, engine) {
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${attacker.displayName}的暴击被${target.displayName}格挡！`);
      } else {
        const damage = 20 * attacker.attackMultiplier;
        target.health -= damage;
        engine.log(`${attacker.displayName}发动暴击！${target.displayName}受到${damage}点重创`);
      }
      attacker.attackMultiplier = 1;
    },
    gain_shield(attacker, target, engine) {
      attacker.shield += 1;
      engine.log(`${attacker.displayName}获得护盾！`);
    },
    self_harm(attacker, target, engine) {
      if (attacker.shield > 0) {
        attacker.shield -= 1;
        engine.log(`${attacker.displayName}的反噬被护盾吸收！`);
      } else {
        attacker.health -= 10;
        engine.log(`${attacker.displayName}受到反噬伤害！失去10点生命值`);
      }
    },
    heal(attacker, target, engine) {
      const healAmount = 10 * attacker.healMultiplier;
      attacker.health += healAmount;
      engine.log(`${attacker.displayName}恢复${healAmount}点生命值`);
      attacker.healMultiplier = 1;
    },
    critical_heal(attacker, target, engine) {
      const healAmount = 20 * attacker.healMultiplier;
      attacker.health += healAmount;
      engine.log(`${attacker.displayName}发动暴疗！恢复${healAmount}点生命值`);
      attacker.healMultiplier = 1;
    },
    two_more(attacker, target, engine) {
      attacker.actionPoints += 2;
      engine.log(`${attacker.displayName}再抽两次！`);
    },
    blood_swap(attacker, target, engine) {
      [attacker.health, target.health] = [target.health, attacker.health];
      engine.log(`${attacker.displayName}发动换血！双方生命值交换`);
    },
    blood_swap1(attacker, target, engine) {
      HANDLERS.blood_swap(attacker, target, engine);
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${attacker.displayName}的追击被${target.displayName}格挡！`);
      } else {
        target.health -= 10;
        engine.log(`${attacker.displayName}追击！${target.displayName}受到10点伤害`);
      }
    },
    silence(attacker, target, engine) {
      if (target.actionPoints >= 0) {
        target.actionPoints -= 3;
        engine.log(`${attacker.displayName}沉默${target.displayName}！行动点减少3点`);
      } else {
        engine.log("无效抽取！请重抽！");
        attacker.actionPoints += 1;
      }
    },
    undead_ultimate(attacker, target, engine) {
      engine.log(`${attacker.displayName}发动终焉之剑！`);
      [attacker.health, target.health] = [target.health, attacker.health];
      engine.log(`${attacker.displayName}发动换血！双方生命值交换`);
      HANDLERS.critical_hit(attacker, target, engine);
      attacker.shield += 1;
      engine.log(`${attacker.displayName}获得护盾！`);
    },
    ice_attack(attacker, target, engine) {
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${attacker.displayName}的寒冰攻击被${target.displayName}格挡！`);
      } else {
        target.health -= 30;
        engine.log(`${attacker.displayName}的寒冰攻击造成30点伤害`);
      }
    },
    ice_silence(attacker, target, engine) {
      target.actionPoints -= 1;
      engine.log(`${attacker.displayName}冰冻${target.displayName}！行动点减少1点`);
    },
    bomb_attack(attacker, target, engine) {
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${target.displayName}格挡爆炸伤害！`);
      } else {
        target.health -= 20;
        engine.log(`${attacker.displayName}的爆炸瓶造成20点伤害`);
      }
      if (attacker.shield > 0) {
        attacker.shield -= 1;
        engine.log(`${attacker.displayName}的护盾吸收自爆伤害！`);
      } else {
        attacker.health -= 20;
        engine.log(`${attacker.displayName}受到20点反冲伤害`);
      }
    },
    double_attack(attacker, target, engine) {
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${attacker.displayName}的燃血轰击被${target.displayName}格挡！`);
      } else {
        target.health -= 20;
        engine.log(`${attacker.displayName}发动燃血轰击！${target.displayName}受到20点重创`);
      }
      if (attacker.shield > 0) {
        attacker.shield -= 1;
        engine.log(`${attacker.displayName}的反噬被护盾吸收！`);
      } else {
        attacker.health -= 10;
        engine.log(`${attacker.displayName}受到反噬伤害！失去10点生命值`);
      }
    },
    poison_attack(attacker, target, engine) {
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${target.displayName}格挡毒液伤害！`);
      } else {
        target.health -= 20;
        engine.log(`${attacker.displayName}的毒瓶造成20点伤害`);
      }
      if (attacker.shield > 0) {
        attacker.shield -= 1;
        engine.log(`${attacker.displayName}的护盾中和毒素！`);
      } else {
        attacker.health -= 30;
        engine.log(`${attacker.displayName}受到30点毒雾反噬`);
      }
    },
    mage_ultimate(attacker, target, engine) {
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${target.displayName}格挡大招伤害！`);
      } else {
        target.health -= 30;
        engine.log(`${attacker.displayName}发动终极魔法！${target.displayName}受到30点伤害`);
      }
      attacker.health += 30;
      engine.log(`${attacker.displayName}恢复30点生命值`);
    },
    medicine_both_heal(attacker, target, engine) {
      const healAmount = 10 * attacker.healMultiplier;
      attacker.health += healAmount;
      target.health += healAmount;
      engine.log(`${attacker.displayName}发动群体治疗！双方各恢复${healAmount}点生命值`);
      attacker.healMultiplier = 1;
    },
    medicine_crit_heal(attacker, target, engine) {
      HANDLERS.critical_hit(attacker, target, engine);
      const healAmount = 10 * attacker.healMultiplier;
      attacker.health += healAmount;
      engine.log(`${attacker.displayName}自我治疗${healAmount}点生命值`);
      attacker.healMultiplier = 1;
    },
    medicine_crit_silence(attacker, target, engine) {
      HANDLERS.critical_hit(attacker, target, engine);
      target.actionPoints -= 1;
      engine.log(`${attacker.displayName}使${target.displayName}下回合无法行动！`);
    },
    medicine_boost_heal(attacker, target, engine) {
      attacker.healMultiplier *= 2;
      engine.log(`${attacker.displayName}强化治疗效果！下次治疗量翻倍！`);
    },
    medicine_mega_heal(attacker, target, engine) {
      const healAmount = 60 * attacker.healMultiplier;
      attacker.health += healAmount;
      engine.log(`${attacker.displayName}发动超级治疗！恢复${healAmount}点生命值`);
      attacker.healMultiplier = 1;
    },
    double_normal_attack(attacker, target, engine) {
      engine.log(`${attacker.displayName}发动双重打击！`);
      const originalMultiplier = attacker.attackMultiplier;
      for (const label of ["第一次", "第二次"]) {
        if (target.shield > 0) {
          target.shield -= 1;
          engine.log(`${attacker.displayName}的${label}攻击被${target.displayName}格挡！`);
        } else {
          const damage = 10 * originalMultiplier;
          target.health -= damage;
          engine.log(`${attacker.displayName}的${label}攻击！${target.displayName}受到${damage}点伤害`);
        }
      }
      attacker.attackMultiplier = 1;
    },
    attack_and_draw(attacker, target, engine) {
      HANDLERS.normal_attack(attacker, target, engine);
      attacker.actionPoints += 1;
      engine.log(`${attacker.displayName}获得额外行动点！`);
    },
    attack_and_heal(attacker, target, engine) {
      HANDLERS.normal_attack(attacker, target, engine);
      HANDLERS.heal(attacker, target, engine);
    },
    attack_and_shield(attacker, target, engine) {
      HANDLERS.normal_attack(attacker, target, engine);
      HANDLERS.gain_shield(attacker, target, engine);
    },
    half_hp_and_attack(attacker, target, engine) {
      HANDLERS.half_hp_both(attacker, target, engine);
      HANDLERS.normal_attack(attacker, target, engine);
    },
    double_next_attack(attacker, target, engine) {
      attacker.attackMultiplier *= 2;
      engine.log(`${attacker.displayName}凝聚力量，下次攻击威力翻倍！`);
    },
    self_harm_and_triple_critical(attacker, target, engine) {
      if (attacker.shield > 0) {
        attacker.shield -= 1;
        engine.log(`${attacker.displayName}的护盾抵挡了生命代价！`);
      } else {
        attacker.health -= 40;
        engine.log(`${attacker.displayName}以自身生命为代价，失去40点生命值！`);
      }
      const originalMultiplier = attacker.attackMultiplier;
      engine.log(`${attacker.displayName}发动毁灭三连击！`);
      for (let index = 1; index <= 3; index += 1) {
        if (target.shield > 0) {
          target.shield -= 1;
          engine.log(`${attacker.displayName}的第${index}次暴击被${target.displayName}格挡！`);
        } else {
          const damage = 20 * originalMultiplier;
          target.health -= damage;
          engine.log(`${attacker.displayName}的第${index}次暴击！${target.displayName}受到${damage}点重创`);
        }
      }
      attacker.attackMultiplier = 1;
    },
    double_deduction(attacker, target, engine) {
      engine.log(`${attacker.displayName}发动双扣10！双方各受10点伤害`);
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${target.displayName}格挡了伤害！`);
      } else {
        target.health -= 10;
        engine.log(`${target.displayName}受到10点伤害`);
      }
      if (attacker.shield > 0) {
        attacker.shield -= 1;
        engine.log(`${attacker.displayName}格挡了自身伤害！`);
      } else {
        attacker.health -= 10;
        engine.log(`${attacker.displayName}受到10点反噬伤害`);
      }
    },
    double_deduction_and_draw(attacker, target, engine) {
      HANDLERS.double_deduction(attacker, target, engine);
      attacker.actionPoints += 1;
      engine.log(`${attacker.displayName}获得额外行动点！`);
    },
    half_hp_both(attacker, target, engine) {
      engine.log(`${attacker.displayName}发动生命削减！双方生命值减半`);
      for (const fighter of [target, attacker]) {
        if (fighter.shield > 0) {
          fighter.shield -= 1;
          engine.log(`${fighter.displayName}的护盾抵挡了生命削减！`);
        } else {
          const newHealth = Math.max(1, Math.floor(fighter.health / 2));
          const damage = fighter.health - newHealth;
          fighter.health = newHealth;
          engine.log(`${fighter.displayName}的生命值被削减一半！失去${damage}点生命值`);
        }
      }
    },
    attack_critical_draw(attacker, target, engine) {
      engine.log(`${attacker.displayName}发动小连招！`);
      HANDLERS.normal_attack(attacker, target, engine);
      HANDLERS.critical_hit(attacker, target, engine);
      attacker.actionPoints += 1;
      engine.log(`${attacker.displayName}获得额外行动点！`);
    },
    double_deduction_30_attack_critical_draw(attacker, target, engine) {
      engine.log(`${attacker.displayName}发动终极连招！`);
      engine.log(`${attacker.displayName}发动双扣30！双方各受30点伤害`);
      if (target.shield > 0) {
        target.shield -= 1;
        engine.log(`${target.displayName}格挡了伤害！`);
      } else {
        target.health -= 30;
        engine.log(`${target.displayName}受到30点伤害`);
      }
      if (attacker.shield > 0) {
        attacker.shield -= 1;
        engine.log(`${attacker.displayName}格挡了自身伤害！`);
      } else {
        attacker.health -= 30;
        engine.log(`${attacker.displayName}受到30点反噬伤害`);
      }
      HANDLERS.normal_attack(attacker, target, engine);
      HANDLERS.critical_hit(attacker, target, engine);
      attacker.actionPoints += 1;
      engine.log(`${attacker.displayName}获得额外行动点！`);
    },
    both_heal_10(attacker, target, engine) {
      engine.log(`${attacker.displayName}发动双加10！双方各恢复10点生命值`);
      attacker.health += 10;
      engine.log(`${attacker.displayName}恢复10点生命值`);
      target.health += 10;
      engine.log(`${target.displayName}恢复10点生命值`);
    },
    critical_and_critical_heal_and_draw(attacker, target, engine) {
      engine.log(`${attacker.displayName}发动终极连招！`);
      HANDLERS.critical_hit(attacker, target, engine);
      HANDLERS.critical_heal(attacker, target, engine);
      HANDLERS.two_more(attacker, target, engine);
    },
    shield_and_self_harm_10(attacker, target, engine) {
      attacker.shield += 1;
      attacker.health -= 10;
      engine.log(`${attacker.displayName}获得护盾，但受到10点不可格挡的反噬伤害！`);
    },
    knight_ultimate(attacker, target, engine) {
      HANDLERS.critical_hit(attacker, target, engine);
      attacker.actionPoints += 1;
      attacker.shield += 3;
      engine.log(`${attacker.displayName}发动终极连招！`);
    },
  };

  const app = {
    mode: "local",
    side: "both",
    peer: null,
    conn: null,
    roomCode: "",
    connected: false,
    selections: {
      p1: { name: "玩家1", characterId: "undead" },
      p2: { name: "玩家2", characterId: "frost" },
    },
    engine: null,
  };

  function selectionFromInputs() {
    app.selections.p1 = {
      name: clampName($("p1Name").value, "玩家1"),
      characterId: $("p1Character").value || "undead",
    };
    app.selections.p2 = {
      name: clampName($("p2Name").value, "玩家2"),
      characterId: $("p2Character").value || "frost",
    };
  }

  function applySelectionsToInputs() {
    $("p1Name").value = app.selections.p1.name;
    $("p1Character").value = app.selections.p1.characterId;
    $("p2Name").value = app.selections.p2.name;
    $("p2Character").value = app.selections.p2.characterId;
    updateCharacterNotes();
  }

  function canEditPlayer(index) {
    if (app.mode === "local") return true;
    if (app.mode === "host") return index === 1;
    if (app.mode === "guest") return index === 2;
    return false;
  }

  function updateInputLocks() {
    $("p1Name").disabled = !canEditPlayer(1) || !!app.engine;
    $("p1Character").disabled = !canEditPlayer(1) || !!app.engine;
    $("p2Name").disabled = !canEditPlayer(2) || !!app.engine;
    $("p2Character").disabled = !canEditPlayer(2) || !!app.engine;
    $("startGame").disabled = app.mode === "guest" || (app.mode === "host" && !app.connected);
    $("copyRoom").disabled = !app.roomCode;
    $("disconnectRoom").disabled = app.mode === "local";
    $("p1Ready").textContent = canEditPlayer(1) ? "可编辑" : "由对方选择";
    $("p2Ready").textContent = canEditPlayer(2) ? "可编辑" : "由对方选择";
  }

  function updateNetworkStatus(text) {
    $("networkStatus").textContent = text;
    $("roomInfo").textContent = text;
  }

  function renderOptions() {
    const options = CHARACTER_DEFS.map((def) => `<option value="${def.id}">${def.name} - ${def.desc}</option>`).join("");
    $("p1Character").innerHTML = options;
    $("p2Character").innerHTML = options;
    $("p1Character").value = app.selections.p1.characterId;
    $("p2Character").value = app.selections.p2.characterId;
  }

  function updateCharacterNotes() {
    for (const [slot, selectId, noteId] of [["p1", "p1Character", "p1Note"], ["p2", "p2Character", "p2Note"]]) {
      const def = CHARACTER_BY_ID[$(selectId).value] || CHARACTER_DEFS[0];
      const total = def.skills.reduce((sum, item) => sum + item[1], 0);
      $(noteId).innerHTML = `<strong>${C.escapeHtml(def.name)}</strong>：${C.escapeHtml(def.desc)}<div class="tags">${def.skills.map(([name, prob, handler]) => `<span class="tag" style="border-color:${SKILL_META[handler]?.[1] || "#d8dee8"}">${C.escapeHtml(name)} ${prob}%</span>`).join("")}</div><div class="small">概率合计 ${total}%</div>`;
      app.selections[slot].characterId = def.id;
    }
  }

  function renderRoster() {
    $("roster").innerHTML = CHARACTER_DEFS.map((def) => {
      const skills = def.skills.map(([name, prob, handler]) => {
        const color = SKILL_META[handler]?.[1] || "#667085";
        return `<span class="tag" style="border-color:${color};color:${color}">${C.escapeHtml(name)} ${prob}%</span>`;
      }).join("");
      return `<article class="game-roster-card"><h3>${C.escapeHtml(def.name)}</h3><p class="small">${C.escapeHtml(def.desc)}</p><div class="tags">${skills}</div></article>`;
    }).join("");
  }

  function renderBattle() {
    const engine = app.engine;
    $("battlePanel").hidden = !engine;
    if (!engine) {
      updateInputLocks();
      return;
    }
    const snapshot = engine.clone();
    $("roundTitle").textContent = `第 ${snapshot.phase} 回合`;
    $("skillBanner").textContent = snapshot.skillText || "等待行动";
    $("skillBanner").style.color = snapshot.skillColor || "#667085";
    snapshot.players.forEach((player, index) => {
      const prefix = index === 0 ? "p1" : "p2";
      $(`${prefix}BattleName`).textContent = player.displayName;
      $(`${prefix}Health`).textContent = Math.max(0, player.health);
      $(`${prefix}Shield`).textContent = player.shield;
      $(`${prefix}Actions`).textContent = player.actionPoints;
      const healthPercent = Math.max(0, Math.min(100, player.health));
      $(`${prefix}HealthBar`).style.width = `${healthPercent}%`;
      $(`fighterP${index + 1}`).classList.toggle("is-current", snapshot.current === index);
      $(`fighterP${index + 1}`).classList.toggle("is-dead", player.health <= 0);
    });
    const current = snapshot.current == null ? null : snapshot.players[snapshot.current];
    $("turnHint").textContent = snapshot.gameOver
      ? "战斗结束"
      : snapshot.roundEnd
        ? "回合结束，等待下一回合"
        : current
          ? `${current.displayName} 行动中`
          : "等待行动";
    $("battleLog").innerHTML = snapshot.logs.map((entry) => `<p ${entry.color ? `style="color:${entry.color}"` : ""}>${C.escapeHtml(entry.text)}</p>`).join("");
    $("battleLog").scrollTop = $("battleLog").scrollHeight;
    updateControls();
    updateInputLocks();
  }

  function localSideCanAct() {
    if (!app.engine || app.engine.gameOver) return false;
    if (app.mode === "local") return true;
    if (app.engine.current == null) return app.mode === "host";
    return (app.mode === "host" && app.engine.current === 0) || (app.mode === "guest" && app.engine.current === 1);
  }

  function updateControls() {
    const engine = app.engine;
    const canAct = localSideCanAct();
    $("actionButton").disabled = !engine || engine.roundEnd || engine.gameOver || !canAct;
    $("nextRoundButton").disabled = !engine || !engine.roundEnd || engine.gameOver || !(app.mode === "local" || app.mode === "host");
    $("restartButton").disabled = !engine || app.mode === "guest";
  }

  function startBattle() {
    selectionFromInputs();
    if (app.mode === "host" && !app.connected) {
      updateNetworkStatus(`房间 ${app.roomCode} 等待访客加入后才能开始。`);
      return;
    }
    app.engine = new BattleEngine(app.selections);
    renderBattle();
    broadcast({ type: "start", selections: app.selections, snapshot: app.engine.clone() });
  }

  function resetBattle() {
    app.engine = null;
    $("battlePanel").hidden = true;
    updateInputLocks();
    broadcast({ type: "reset", selections: app.selections });
  }

  function performAction() {
    if (!app.engine) return;
    if (app.mode === "guest") {
      send({ type: "intent", action: "takeAction" });
      return;
    }
    app.engine.takeAction();
    renderBattle();
    broadcastSnapshot();
  }

  function performNextRound() {
    if (!app.engine) return;
    if (app.mode === "guest") {
      send({ type: "intent", action: "nextRound" });
      return;
    }
    app.engine.nextRound();
    renderBattle();
    broadcastSnapshot();
  }

  function send(message) {
    if (app.conn?.open) app.conn.send(message);
  }

  function broadcast(message) {
    if (app.mode === "host") send(message);
  }

  function broadcastSnapshot() {
    broadcast({ type: "snapshot", snapshot: app.engine?.clone() });
  }

  function createRoomCode() {
    return Math.random().toString(36).slice(2, 7).toUpperCase();
  }

  function peerIdFromRoom(code) {
    return `scpper-mc-${String(code || "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "")}`;
  }

  function closeNetwork() {
    try { app.conn?.close(); } catch {}
    try { app.peer?.destroy(); } catch {}
    app.peer = null;
    app.conn = null;
    app.connected = false;
    app.roomCode = "";
    app.mode = "local";
    app.side = "both";
    updateNetworkStatus("本地模式");
    updateInputLocks();
  }

  function ensurePeerJs() {
    if (!window.Peer) {
      updateNetworkStatus("联机库暂时没有加载成功，可以先使用本地双人。");
      return false;
    }
    return true;
  }

  function hostRoom() {
    if (!ensurePeerJs()) return;
    closeNetwork();
    selectionFromInputs();
    app.mode = "host";
    app.side = "p1";
    app.roomCode = createRoomCode();
    const peerId = peerIdFromRoom(app.roomCode);
    app.peer = new Peer(peerId, { debug: 1 });
    app.peer.on("open", () => {
      updateNetworkStatus(`房间已创建：${app.roomCode}。把房间码发给对手。`);
      updateInputLocks();
    });
    app.peer.on("connection", (conn) => {
      if (app.conn?.open) {
        conn.on("open", () => conn.send({ type: "error", message: "房间已满" }));
        setTimeout(() => conn.close(), 300);
        return;
      }
      setupConnection(conn);
      app.connected = true;
      updateNetworkStatus(`房间 ${app.roomCode} 已连接，对手正在选择角色。`);
      syncSelections();
    });
    app.peer.on("error", (err) => {
      updateNetworkStatus(`创建房间失败：${err.message || err.type || err}`);
      updateInputLocks();
    });
    updateInputLocks();
  }

  function joinRoom() {
    if (!ensurePeerJs()) return;
    const code = $("roomInput").value.trim();
    if (!code) {
      updateNetworkStatus("请输入房间码。");
      return;
    }
    closeNetwork();
    selectionFromInputs();
    app.mode = "guest";
    app.side = "p2";
    app.roomCode = code.toUpperCase();
    app.peer = new Peer(undefined, { debug: 1 });
    app.peer.on("open", () => {
      updateNetworkStatus(`正在加入房间 ${app.roomCode}...`);
      const conn = app.peer.connect(peerIdFromRoom(app.roomCode), { reliable: true });
      setupConnection(conn);
    });
    app.peer.on("error", (err) => {
      updateNetworkStatus(`加入房间失败：${err.message || err.type || err}`);
      updateInputLocks();
    });
    updateInputLocks();
  }

  function setupConnection(conn) {
    app.conn = conn;
    conn.on("open", () => {
      app.connected = true;
      updateNetworkStatus(app.mode === "host" ? `房间 ${app.roomCode} 已连接。` : `已加入房间 ${app.roomCode}，等待房主开始。`);
      updateInputLocks();
      if (app.mode === "guest") send({ type: "selection", selection: app.selections.p2 });
      if (app.mode === "host") syncSelections();
    });
    conn.on("data", handleMessage);
    conn.on("close", () => {
      app.connected = false;
      updateNetworkStatus("联机已断开，可以重新创建或加入房间。");
      updateInputLocks();
    });
    conn.on("error", (err) => {
      updateNetworkStatus(`联机错误：${err.message || err.type || err}`);
      updateInputLocks();
    });
  }

  function handleMessage(message) {
    if (!message || typeof message !== "object") return;
    if (message.type === "selection" && app.mode === "host") {
      app.selections.p2 = {
        name: clampName(message.selection?.name, "玩家2"),
        characterId: message.selection?.characterId || "frost",
      };
      applySelectionsToInputs();
      syncSelections();
      return;
    }
    if (message.type === "selectionState" && app.mode === "guest") {
      app.selections = message.selections;
      applySelectionsToInputs();
      updateNetworkStatus(`已加入房间 ${app.roomCode}，等待房主开始。`);
      updateInputLocks();
      return;
    }
    if (message.type === "start" && app.mode === "guest") {
      app.selections = message.selections;
      app.engine = BattleEngine.fromSnapshot(message.snapshot);
      applySelectionsToInputs();
      renderBattle();
      return;
    }
    if (message.type === "snapshot" && app.mode === "guest") {
      app.engine = BattleEngine.fromSnapshot(message.snapshot);
      renderBattle();
      return;
    }
    if (message.type === "reset" && app.mode === "guest") {
      app.selections = message.selections || app.selections;
      app.engine = null;
      applySelectionsToInputs();
      $("battlePanel").hidden = true;
      updateInputLocks();
      return;
    }
    if (message.type === "intent" && app.mode === "host") {
      if (!app.engine) return;
      if (message.action === "takeAction" && app.engine.current === 1) {
        app.engine.takeAction();
      } else if (message.action === "nextRound" && app.engine.roundEnd) {
        app.engine.nextRound();
      }
      renderBattle();
      broadcastSnapshot();
      return;
    }
    if (message.type === "error") {
      updateNetworkStatus(message.message || "联机错误");
    }
  }

  function syncSelections() {
    if (app.mode !== "host") return;
    selectionFromInputs();
    send({ type: "selectionState", selections: app.selections });
  }

  function pushOwnSelection() {
    selectionFromInputs();
    updateCharacterNotes();
    if (app.mode === "guest") {
      send({ type: "selection", selection: app.selections.p2 });
    } else if (app.mode === "host") {
      syncSelections();
    }
  }

  async function copyRoom() {
    if (!app.roomCode) return;
    try {
      await navigator.clipboard.writeText(app.roomCode);
      updateNetworkStatus(`房间码 ${app.roomCode} 已复制。`);
    } catch {
      updateNetworkStatus(`房间码：${app.roomCode}`);
    }
  }

  function bindEvents() {
    $("localMode").addEventListener("click", closeNetwork);
    $("hostRoom").addEventListener("click", hostRoom);
    $("joinRoom").addEventListener("click", joinRoom);
    $("copyRoom").addEventListener("click", copyRoom);
    $("disconnectRoom").addEventListener("click", closeNetwork);
    $("startGame").addEventListener("click", startBattle);
    $("actionButton").addEventListener("click", performAction);
    $("nextRoundButton").addEventListener("click", performNextRound);
    $("restartButton").addEventListener("click", resetBattle);
    for (const id of ["p1Name", "p1Character", "p2Name", "p2Character"]) {
      $(id).addEventListener("input", pushOwnSelection);
      $(id).addEventListener("change", pushOwnSelection);
    }
  }

  function init() {
    $("nav").innerHTML = C.renderNav("game");
    C.wireRefresh();
    C.tickBeijing("nowBeijing");
    renderOptions();
    renderRoster();
    bindEvents();
    applySelectionsToInputs();
    updateInputLocks();
    renderBattle();
  }

  init();
})();
